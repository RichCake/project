from django import forms

from accounts.models import User
from .models import ChatRoom, SurveyTemplate, SurveyQuestion


class CreateChatForm(forms.Form):
    candidate = forms.ModelChoiceField(
        queryset=User.objects.filter(role=User.Role.CANDIDATE),
        label="Кандидат",
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class SendSurveyForm(forms.Form):
    template = forms.ModelChoiceField(
        queryset=SurveyTemplate.objects.all(),
        label="Шаблон опроса",
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class SurveyTemplateForm(forms.ModelForm):
    class Meta:
        model = SurveyTemplate
        fields = ("title",)
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
        }


class SurveyQuestionForm(forms.ModelForm):
    options_text = forms.CharField(
        label="Варианты (по одному на строку)",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    class Meta:
        model = SurveyQuestion
        fields = ("text", "question_type")
        widgets = {
            "text": forms.TextInput(attrs={"class": "form-control"}),
            "question_type": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.options:
            self.fields["options_text"].initial = "\n".join(self.instance.options)
