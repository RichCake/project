import json

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import manager_required
from .forms import CreateChatForm, SendSurveyForm, SurveyTemplateForm, SurveyQuestionForm
from .models import ChatRoom, Message, Survey, SurveyAnswer, SurveyQuestion, SurveyTemplate


@login_required
def room_list(request):
    user = request.user
    if user.is_candidate:
        rooms = ChatRoom.objects.filter(candidate=user)
        if rooms.count() == 1:
            return redirect("chat:room_detail", pk=rooms.first().pk)
    else:
        rooms = ChatRoom.objects.filter(recruiter=user)

    create_form = None
    if user.can_manage:
        create_form = CreateChatForm()
        if request.method == "POST" and "create_chat" in request.POST:
            create_form = CreateChatForm(request.POST)
            if create_form.is_valid():
                candidate = create_form.cleaned_data["candidate"]
                room, created = ChatRoom.objects.get_or_create(
                    candidate=candidate, recruiter=user
                )
                return redirect("chat:room_detail", pk=room.pk)

    return render(request, "chat/room_list.html", {
        "rooms": rooms,
        "create_form": create_form,
    })


@login_required
def room_detail(request, pk):
    room = get_object_or_404(ChatRoom, pk=pk)
    user = request.user

    if user.is_candidate and room.candidate != user:
        raise PermissionDenied
    if user.can_manage and room.recruiter != user:
        raise PermissionDenied

    send_survey_form = None
    if user.can_manage:
        send_survey_form = SendSurveyForm()

    room_messages = room.messages.select_related("sender").all()
    surveys = Survey.objects.filter(chat_room=room).select_related("template")

    return render(request, "chat/room.html", {
        "room": room,
        "room_messages": room_messages,
        "send_survey_form": send_survey_form,
        "surveys": surveys,
    })


@login_required
def api_messages(request, pk):
    """AJAX polling endpoint: returns messages after a given ID."""
    room = get_object_or_404(ChatRoom, pk=pk)
    user = request.user

    if user.is_candidate and room.candidate != user:
        return JsonResponse({"error": "forbidden"}, status=403)
    if user.can_manage and room.recruiter != user:
        return JsonResponse({"error": "forbidden"}, status=403)

    after = request.GET.get("after", 0)
    try:
        after = int(after)
    except (ValueError, TypeError):
        after = 0

    msgs = room.messages.filter(id__gt=after).select_related("sender")
    data = []
    for m in msgs:
        data.append({
            "id": m.id,
            "sender": str(m.sender) if m.sender else "Система",
            "sender_id": m.sender_id,
            "text": m.text,
            "is_system": m.is_system,
            "created_at": timezone.localtime(m.created_at).strftime("%H:%M"),
        })
    return JsonResponse({"messages": data})


@require_POST
@login_required
def api_send(request, pk):
    """AJAX endpoint to send a message."""
    room = get_object_or_404(ChatRoom, pk=pk)
    user = request.user

    if user.is_candidate and room.candidate != user:
        return JsonResponse({"error": "forbidden"}, status=403)
    if user.can_manage and room.recruiter != user:
        return JsonResponse({"error": "forbidden"}, status=403)

    text = request.POST.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "empty"}, status=400)

    msg = Message.objects.create(room=room, sender=user, text=text)
    return JsonResponse({
        "id": msg.id,
        "sender": str(user),
        "text": msg.text,
        "created_at": timezone.localtime(msg.created_at).strftime("%H:%M"),
    })


@manager_required
def send_survey(request, pk):
    """HR sends a survey to the candidate in a chat room."""
    room = get_object_or_404(ChatRoom, pk=pk)

    if request.method == "POST":
        form = SendSurveyForm(request.POST)
        if form.is_valid():
            template = form.cleaned_data["template"]
            survey = Survey.objects.create(
                template=template,
                chat_room=room,
                candidate=room.candidate,
                status=Survey.Status.PENDING,
            )
            Message.objects.create(
                room=room,
                sender=None,
                text=f"Вам назначен опрос: «{template.title}». Пожалуйста, заполните его.",
                is_system=True,
            )
            django_messages.success(request, "Опрос отправлен.")
            return redirect("chat:room_detail", pk=room.pk)

    return redirect("chat:room_detail", pk=room.pk)


@login_required
def survey_fill(request, pk):
    survey = get_object_or_404(Survey, pk=pk)
    user = request.user

    if user != survey.candidate:
        raise PermissionDenied

    questions = survey.template.questions.all()

    if request.method == "POST":
        for q in questions:
            answer = request.POST.get(f"q_{q.id}", "")
            SurveyAnswer.objects.update_or_create(
                survey=survey, question=q,
                defaults={"answer_text": answer},
            )
        survey.status = Survey.Status.COMPLETED
        survey.completed_at = timezone.now()
        survey.save()

        if survey.chat_room:
            Message.objects.create(
                room=survey.chat_room,
                sender=None,
                text=f"Кандидат завершил опрос «{survey.template.title}».",
                is_system=True,
            )

        django_messages.success(request, "Опрос заполнен.")
        if survey.chat_room:
            return redirect("chat:room_detail", pk=survey.chat_room.pk)
        return redirect("home")

    existing_answers = {a.question_id: a.answer_text for a in survey.answers.all()}

    return render(request, "chat/survey_fill.html", {
        "survey": survey,
        "questions": questions,
        "existing_answers": existing_answers,
    })


@login_required
def survey_results(request, pk):
    survey = get_object_or_404(Survey, pk=pk)
    user = request.user

    if user.is_candidate and user != survey.candidate:
        raise PermissionDenied

    answers = survey.answers.select_related("question").all()
    return render(request, "chat/survey_results.html", {
        "survey": survey,
        "answers": answers,
    })


def _apply_question_options(question, options_text):
    if options_text:
        question.options = [o.strip() for o in options_text.strip().split("\n") if o.strip()]
    else:
        question.options = []


def _next_question_order(template):
    max_order = template.questions.aggregate(m=Max("order"))["m"]
    return (max_order or 0) + 1


def _normalize_question_order(template):
    for idx, q in enumerate(template.questions.order_by("order", "pk"), start=1):
        if q.order != idx:
            SurveyQuestion.objects.filter(pk=q.pk).update(order=idx)


def _move_question(template, question_id, direction):
    ordered = list(template.questions.order_by("order", "pk"))
    try:
        idx = next(i for i, q in enumerate(ordered) if q.pk == int(question_id))
    except (StopIteration, ValueError, TypeError):
        return False
    new_idx = idx + direction
    if new_idx < 0 or new_idx >= len(ordered):
        return False
    ordered[idx], ordered[new_idx] = ordered[new_idx], ordered[idx]
    for i, q in enumerate(ordered, start=1):
        q.order = i
    SurveyQuestion.objects.bulk_update(ordered, ["order"])
    return True


@manager_required
def survey_template_list(request):
    templates = SurveyTemplate.objects.all()
    return render(request, "chat/survey_template_list.html", {"templates": templates})


@require_POST
@manager_required
def survey_template_delete(request, pk):
    tmpl = get_object_or_404(SurveyTemplate, pk=pk)
    title = tmpl.title
    tmpl.delete()
    django_messages.success(request, f"Шаблон «{title}» удалён.")
    return redirect("chat:survey_template_list")


@manager_required
def survey_template_create(request):
    if request.method == "POST":
        form = SurveyTemplateForm(request.POST)
        if form.is_valid():
            tmpl = form.save(commit=False)
            tmpl.created_by = request.user
            tmpl.save()
            django_messages.success(request, "Шаблон создан.")
            return redirect("chat:survey_template_edit", pk=tmpl.pk)
    else:
        form = SurveyTemplateForm()
    return render(request, "chat/survey_template_form.html", {"form": form, "is_new": True})


@manager_required
def survey_template_edit(request, pk):
    tmpl = get_object_or_404(SurveyTemplate, pk=pk)
    questions = tmpl.questions.all()
    editing_question_id = request.GET.get("edit")

    if request.method == "POST":
        if "add_question" in request.POST:
            q_form = SurveyQuestionForm(request.POST)
            if q_form.is_valid():
                q = q_form.save(commit=False)
                q.template = tmpl
                q.order = _next_question_order(tmpl)
                _apply_question_options(q, q_form.cleaned_data.get("options_text", ""))
                q.save()
                django_messages.success(request, "Вопрос добавлен.")
                return redirect("chat:survey_template_edit", pk=pk)
        elif "update_question" in request.POST:
            q = get_object_or_404(SurveyQuestion, pk=request.POST.get("question_id"), template=tmpl)
            q_form = SurveyQuestionForm(request.POST, instance=q)
            if q_form.is_valid():
                q = q_form.save(commit=False)
                _apply_question_options(q, q_form.cleaned_data.get("options_text", ""))
                q.save()
                django_messages.success(request, "Вопрос обновлён.")
                return redirect("chat:survey_template_edit", pk=pk)
        elif "delete_question" in request.POST:
            SurveyQuestion.objects.filter(
                pk=request.POST.get("question_id"), template=tmpl
            ).delete()
            _normalize_question_order(tmpl)
            django_messages.success(request, "Вопрос удалён.")
            return redirect("chat:survey_template_edit", pk=pk)
        elif "move_question_up" in request.POST:
            if _move_question(tmpl, request.POST.get("question_id"), -1):
                django_messages.success(request, "Порядок вопросов изменён.")
            return redirect("chat:survey_template_edit", pk=pk)
        elif "move_question_down" in request.POST:
            if _move_question(tmpl, request.POST.get("question_id"), 1):
                django_messages.success(request, "Порядок вопросов изменён.")
            return redirect("chat:survey_template_edit", pk=pk)

    q_form = SurveyQuestionForm()
    edit_q_form = None
    editing_question_pk = None
    if editing_question_id:
        edit_q = get_object_or_404(SurveyQuestion, pk=editing_question_id, template=tmpl)
        edit_q_form = SurveyQuestionForm(instance=edit_q)
        editing_question_pk = edit_q.pk

    return render(request, "chat/survey_template_form.html", {
        "q_form": q_form,
        "edit_q_form": edit_q_form,
        "editing_question_id": editing_question_pk,
        "template_obj": tmpl,
        "questions": questions,
        "is_new": False,
    })
