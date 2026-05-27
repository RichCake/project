from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from chat.forms import CreateChatForm, SurveyQuestionForm
from chat.models import ChatRoom, Message, Survey, SurveyAnswer, SurveyQuestion, SurveyTemplate
from tests.factories import (
    DEFAULT_PASSWORD,
    create_chat_room,
    create_message,
    create_survey_question,
    create_survey_template,
    create_user,
)


class ChatModelAndFormTests(TestCase):
    def setUp(self):
        self.candidate = create_user("chat_candidate", role=User.Role.CANDIDATE)
        self.hr = create_user("chat_hr", role=User.Role.HR)

    def test_chat_room_last_message_returns_latest(self):
        room = create_chat_room(self.candidate, self.hr)
        first = create_message(room, self.hr, text="one")
        last = create_message(room, self.candidate, text="two")

        self.assertEqual(room.last_message, last)
        self.assertNotEqual(room.last_message, first)

    def test_create_chat_form_contains_only_candidates(self):
        create_user("chat_director", role=User.Role.DIRECTOR)
        form = CreateChatForm()
        self.assertEqual(list(form.fields["candidate"].queryset), [self.candidate])

    def test_survey_question_form_valid(self):
        form = SurveyQuestionForm(
            data={"text": "Ваш опыт?", "question_type": SurveyQuestion.QuestionType.TEXT}
        )
        self.assertTrue(form.is_valid())


class ChatViewTests(TestCase):
    def setUp(self):
        self.candidate = create_user("candidate_chat_view", role=User.Role.CANDIDATE)
        self.other_candidate = create_user("other_candidate_chat", role=User.Role.CANDIDATE)
        self.hr = create_user("hr_chat_view", role=User.Role.HR)
        self.other_hr = create_user("other_hr_chat", role=User.Role.HR)
        self.room = create_chat_room(self.candidate, self.hr)
        self.template = create_survey_template(self.hr, title="Первичное интервью")
        self.question = create_survey_question(self.template, order=1, text="Почему вы?", options=[])
        self.message = create_message(self.room, self.hr, text="Здравствуйте")

    def test_candidate_with_single_room_redirects_to_room(self):
        self.client.login(username=self.candidate.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("chat:room_list"))
        self.assertRedirects(response, reverse("chat:room_detail", kwargs={"pk": self.room.pk}))

    def test_hr_can_create_chat_room(self):
        self.client.login(username=self.hr.username, password=DEFAULT_PASSWORD)
        response = self.client.post(
            reverse("chat:room_list"),
            {"create_chat": "1", "candidate": self.other_candidate.pk},
        )
        created_room = ChatRoom.objects.get(candidate=self.other_candidate, recruiter=self.hr)
        self.assertRedirects(response, reverse("chat:room_detail", kwargs={"pk": created_room.pk}))

    def test_room_detail_denies_non_participant_candidate(self):
        self.client.login(username=self.other_candidate.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("chat:room_detail", kwargs={"pk": self.room.pk}))
        self.assertEqual(response.status_code, 403)

    def test_room_detail_denies_other_hr(self):
        self.client.login(username=self.other_hr.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("chat:room_detail", kwargs={"pk": self.room.pk}))
        self.assertEqual(response.status_code, 403)

    def test_api_messages_returns_messages_after_id(self):
        second = create_message(self.room, self.candidate, text="Ответ")
        self.client.login(username=self.hr.username, password=DEFAULT_PASSWORD)
        response = self.client.get(
            reverse("chat:api_messages", kwargs={"pk": self.room.pk}),
            {"after": self.message.id},
        )
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["messages"][0]["id"], second.id)

    def test_api_messages_forbidden_for_outsider(self):
        self.client.login(username=self.other_candidate.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("chat:api_messages", kwargs={"pk": self.room.pk}))
        self.assertEqual(response.status_code, 403)

    def test_api_send_creates_message(self):
        self.client.login(username=self.candidate.username, password=DEFAULT_PASSWORD)
        response = self.client.post(
            reverse("chat:api_send", kwargs={"pk": self.room.pk}),
            {"text": "Новое сообщение"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Message.objects.filter(room=self.room, sender=self.candidate, text="Новое сообщение").exists()
        )

    def test_api_send_rejects_empty_message(self):
        self.client.login(username=self.candidate.username, password=DEFAULT_PASSWORD)
        response = self.client.post(
            reverse("chat:api_send", kwargs={"pk": self.room.pk}),
            {"text": "   "},
        )
        self.assertEqual(response.status_code, 400)

    def test_send_survey_creates_survey_and_system_message(self):
        self.client.login(username=self.hr.username, password=DEFAULT_PASSWORD)
        response = self.client.post(
            reverse("chat:send_survey", kwargs={"pk": self.room.pk}),
            {"template": self.template.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        survey = Survey.objects.get(chat_room=self.room, candidate=self.candidate)
        self.assertEqual(survey.template, self.template)
        self.assertTrue(Message.objects.filter(room=self.room, is_system=True).exists())

    def test_candidate_can_fill_survey(self):
        survey = Survey.objects.create(template=self.template, chat_room=self.room, candidate=self.candidate)
        self.client.login(username=self.candidate.username, password=DEFAULT_PASSWORD)
        response = self.client.post(
            reverse("chat:survey_fill", kwargs={"pk": survey.pk}),
            {f"q_{self.question.id}": "Потому что у меня релевантный опыт"},
            follow=True,
        )
        survey.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(survey.status, Survey.Status.COMPLETED)
        self.assertIsNotNone(survey.completed_at)
        self.assertTrue(
            SurveyAnswer.objects.filter(
                survey=survey,
                question=self.question,
                answer_text="Потому что у меня релевантный опыт",
            ).exists()
        )
        self.assertTrue(
            Message.objects.filter(
                room=self.room,
                is_system=True,
                text=f"Кандидат завершил опрос «{self.template.title}».",
            ).exists()
        )

    def test_other_candidate_cannot_fill_foreign_survey(self):
        survey = Survey.objects.create(template=self.template, chat_room=self.room, candidate=self.candidate)
        self.client.login(username=self.other_candidate.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("chat:survey_fill", kwargs={"pk": survey.pk}))
        self.assertEqual(response.status_code, 403)

    def test_manager_can_create_and_delete_question_in_template(self):
        self.client.login(username=self.hr.username, password=DEFAULT_PASSWORD)
        response = self.client.post(
            reverse("chat:survey_template_edit", kwargs={"pk": self.template.pk}),
            {
                "add_question": "1",
                "text": "Готовы начать?",
                "question_type": SurveyQuestion.QuestionType.YES_NO,
                "options_text": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        created_question = SurveyQuestion.objects.get(template=self.template, text="Готовы начать?")

        response = self.client.post(
            reverse("chat:survey_template_edit", kwargs={"pk": self.template.pk}),
            {"delete_question": "1", "question_id": created_question.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SurveyQuestion.objects.filter(pk=created_question.pk).exists())

    def test_manager_can_update_reorder_and_delete_template(self):
        q2 = create_survey_question(
            self.template, order=2, text="Второй вопрос", options=[]
        )
        self.client.login(username=self.hr.username, password=DEFAULT_PASSWORD)

        response = self.client.post(
            reverse("chat:survey_template_edit", kwargs={"pk": self.template.pk}),
            {
                "update_question": "1",
                "question_id": self.question.pk,
                "text": "Обновлённый вопрос",
                "question_type": SurveyQuestion.QuestionType.TEXT,
                "options_text": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.text, "Обновлённый вопрос")

        response = self.client.post(
            reverse("chat:survey_template_edit", kwargs={"pk": self.template.pk}),
            {"move_question_down": "1", "question_id": self.question.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.question.refresh_from_db()
        q2.refresh_from_db()
        self.assertEqual(self.question.order, 2)
        self.assertEqual(q2.order, 1)

        template_pk = self.template.pk
        response = self.client.post(
            reverse("chat:survey_template_delete", kwargs={"pk": template_pk}),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SurveyTemplate.objects.filter(pk=template_pk).exists())
