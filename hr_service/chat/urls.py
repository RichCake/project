from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.room_list, name="room_list"),
    path("<int:pk>/", views.room_detail, name="room_detail"),
    path("api/messages/<int:pk>/", views.api_messages, name="api_messages"),
    path("api/send/<int:pk>/", views.api_send, name="api_send"),
    path("<int:pk>/send-survey/", views.send_survey, name="send_survey"),
    path("survey/<int:pk>/", views.survey_fill, name="survey_fill"),
    path("survey/<int:pk>/results/", views.survey_results, name="survey_results"),
    path("templates/", views.survey_template_list, name="survey_template_list"),
    path("templates/create/", views.survey_template_create, name="survey_template_create"),
    path("templates/<int:pk>/edit/", views.survey_template_edit, name="survey_template_edit"),
    path("templates/<int:pk>/delete/", views.survey_template_delete, name="survey_template_delete"),
]
