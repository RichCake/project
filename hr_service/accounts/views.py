from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render

from .forms import LoginForm, ProfileForm

POLICY_TEXT_PATH = Path(settings.BASE_DIR) / "legal" / "policy_text.txt"


def _load_policy_text() -> str:
    try:
        return POLICY_TEXT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def privacy_policy_view(request):
    return render(
        request,
        "legal/privacy_policy.html",
        {"policy_text": _load_policy_text()},
    )


class CustomLoginView(LoginView):
    form_class = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class CustomLogoutView(LogoutView):
    next_page = "accounts:login"


@login_required
def home_view(request):
    user = request.user
    if user.is_candidate:
        return redirect("vacancies:vacancy_list")
    if user.can_view_analytics:
        return redirect("analytics:dashboard")
    return redirect("vacancies:vacancy_list")


@login_required
def profile_view(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Профиль обновлён.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)
    return render(request, "accounts/profile.html", {"form": form})
