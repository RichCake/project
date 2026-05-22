import io
import json
from collections import Counter
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.decorators import analytics_required
from accounts.models import User
from vacancies.models import Application, Interview, Vacancy

import openpyxl
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


def _funnel_data():
    """Counts for each application status to build a hiring funnel."""
    statuses = ["new", "screening", "interview", "offer", "hired", "rejected"]
    labels = ["Новый", "Скрининг", "Собеседование", "Оффер", "Нанят", "Отклонён"]
    counts = []
    for s in statuses:
        counts.append(Application.objects.filter(status=s).count())
    return labels, counts


def _time_to_hire():
    """Average days to hire per month (last 6 months)."""
    now = timezone.now()
    labels, values = [], []
    for i in range(5, -1, -1):
        month_start = (now - timedelta(days=30 * i)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if i > 0:
            month_end = (now - timedelta(days=30 * (i - 1))).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            month_end = now

        hired = Application.objects.filter(
            status="hired",
            applied_at__gte=month_start,
            applied_at__lt=month_end,
        )
        if hired.exists():
            avg_days = 0
            count = 0
            for a in hired:
                last_interview = a.interviews.filter(status="completed").order_by("-scheduled_at").first()
                if last_interview:
                    avg_days += (last_interview.scheduled_at - a.applied_at).days
                    count += 1
            values.append(round(avg_days / count, 1) if count else 0)
        else:
            values.append(0)
        labels.append(month_start.strftime("%b %Y"))
    return labels, values


def _vacancies_by_department():
    """Active vacancies grouped by department."""
    qs = Vacancy.objects.filter(status="open").values("department").annotate(count=Count("id"))
    labels = [item["department"] for item in qs]
    counts = [item["count"] for item in qs]
    return labels, counts


def _conversion_rates():
    """Conversion % between pipeline stages."""
    total = Application.objects.count() or 1
    stages = [
        ("new", "Новый"),
        ("screening", "Скрининг"),
        ("interview", "Собеседование"),
        ("offer", "Оффер"),
        ("hired", "Нанят"),
    ]
    labels, values = [], []
    for status, label in stages:
        count = Application.objects.filter(status=status).count()
        labels.append(label)
        values.append(round(count / total * 100, 1))
    return labels, values


def _heatmap_data():
    """Interview counts by day-of-week and hour."""
    grid = [[0] * 24 for _ in range(7)]
    for iv in Interview.objects.filter(status__in=["scheduled", "completed"]):
        dt = timezone.localtime(iv.scheduled_at)
        grid[dt.weekday()][dt.hour] += 1
    return grid


def _kpi_cards():
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "total_vacancies": Vacancy.objects.filter(status="open").count(),
        "total_candidates": User.objects.filter(role="candidate").count(),
        "hired_this_month": Application.objects.filter(status="hired", applied_at__gte=month_start).count(),
        "total_interviews_scheduled": Interview.objects.filter(status="scheduled").count(),
    }


@analytics_required
def dashboard(request):
    funnel_labels, funnel_data = _funnel_data()
    tth_labels, tth_data = _time_to_hire()
    dept_labels, dept_data = _vacancies_by_department()
    conv_labels, conv_data = _conversion_rates()
    heatmap = _heatmap_data()
    kpi = _kpi_cards()

    context = {
        "funnel_labels": json.dumps(funnel_labels),
        "funnel_data": json.dumps(funnel_data),
        "tth_labels": json.dumps(tth_labels),
        "tth_data": json.dumps(tth_data),
        "dept_labels": json.dumps(dept_labels),
        "dept_data": json.dumps(dept_data),
        "conv_labels": json.dumps(conv_labels),
        "conv_data": json.dumps(conv_data),
        "heatmap": json.dumps(heatmap),
        "kpi": kpi,
    }
    return render(request, "analytics/dashboard.html", context)


@analytics_required
def generate_report_excel(request):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Отчёт по вакансиям"

    headers = ["Вакансия", "Отдел", "Статус", "Откликов", "На собеседовании", "Нанято"]
    ws.append(headers)

    for v in Vacancy.objects.all():
        apps = v.applications
        ws.append([
            v.title,
            v.department,
            v.get_status_display(),
            apps.count(),
            apps.filter(status="interview").count(),
            apps.filter(status="hired").count(),
        ])

    ws.append([])
    ws.append(["Сводка"])
    kpi = _kpi_cards()
    ws.append(["Открытых вакансий", kpi["total_vacancies"]])
    ws.append(["Всего кандидатов", kpi["total_candidates"]])
    ws.append(["Нанято за месяц", kpi["hired_this_month"]])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="hr_report.xlsx"'
    return response


@analytics_required
def generate_report_pdf(request):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("HR Report / Отчёт", styles["Title"]))
    elements.append(Spacer(1, 10 * mm))

    kpi = _kpi_cards()
    kpi_data = [
        ["Metric", "Value"],
        ["Open vacancies", str(kpi["total_vacancies"])],
        ["Total candidates", str(kpi["total_candidates"])],
        ["Hired this month", str(kpi["hired_this_month"])],
        ["Scheduled interviews", str(kpi["total_interviews_scheduled"])],
    ]
    t = Table(kpi_data, colWidths=[120 * mm, 40 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 10 * mm))

    vacancy_data = [["Vacancy", "Department", "Status", "Applications", "Hired"]]
    for v in Vacancy.objects.all():
        apps = v.applications
        vacancy_data.append([
            v.title, v.department, v.get_status_display(),
            str(apps.count()), str(apps.filter(status="hired").count()),
        ])
    if len(vacancy_data) > 1:
        t2 = Table(vacancy_data, colWidths=[45 * mm, 35 * mm, 30 * mm, 25 * mm, 25 * mm])
        t2.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3b82f6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        elements.append(t2)

    doc.build(elements)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="hr_report.pdf"'
    return response
