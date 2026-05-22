import io
import json
import os
from urllib.parse import urlencode

import openpyxl
import reportlab
from django.http import HttpResponse
from django.shortcuts import render
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from accounts.decorators import analytics_required

from .services import FUNNEL_STAGES, build_dashboard_payload, parse_filters


_PDF_FONT_REGISTRY = None


def _ensure_pdf_font():
    """Register a Unicode TTF font (regular + bold) for Cyrillic PDF output."""
    global _PDF_FONT_REGISTRY
    if _PDF_FONT_REGISTRY is not None:
        return _PDF_FONT_REGISTRY

    reportlab_fonts_dir = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    candidates = [
        ("DejaVuSans", os.path.join(reportlab_fonts_dir, "DejaVuSans.ttf"), os.path.join(reportlab_fonts_dir, "DejaVuSans-Bold.ttf")),
        ("Arial", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("Calibri", "C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
        ("DejaVuLinux", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("LiberationLinux", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ("Vera", os.path.join(reportlab_fonts_dir, "Vera.ttf"), os.path.join(reportlab_fonts_dir, "VeraBd.ttf")),
    ]
    for family, regular_path, bold_path in candidates:
        if not os.path.exists(regular_path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(family, regular_path))
            bold_name = family
            if bold_path and os.path.exists(bold_path):
                try:
                    pdfmetrics.registerFont(TTFont(f"{family}-Bold", bold_path))
                    bold_name = f"{family}-Bold"
                except Exception:
                    pass
            pdfmetrics.registerFontFamily(
                family, normal=family, bold=bold_name, italic=family, boldItalic=bold_name
            )
            _PDF_FONT_REGISTRY = {"regular": family, "bold": bold_name}
            return _PDF_FONT_REGISTRY
        except Exception:
            continue
    _PDF_FONT_REGISTRY = {"regular": "Helvetica", "bold": "Helvetica-Bold"}
    return _PDF_FONT_REGISTRY


@analytics_required
def dashboard(request):
    filters = parse_filters(request.GET)
    payload = build_dashboard_payload(filters)
    query_data = request.GET.copy()
    query_data.pop("drilldown", None)
    query_data.pop("value", None)
    export_query = urlencode(query_data, doseq=True)
    base_filter_query = urlencode(
        {
            "period": payload["selected_filters"]["period"],
            "department": payload["selected_filters"]["department"],
            "vacancy_status": payload["selected_filters"]["vacancy_status"],
            "application_status": payload["selected_filters"]["application_status"],
            "vacancy_id": payload["selected_filters"]["vacancy_id"],
        }
    )
    context = {
        "funnel_labels": json.dumps(payload["funnel_labels"]),
        "funnel_data": json.dumps(payload["funnel_data"]),
        "tth_labels": json.dumps(payload["tth_labels"]),
        "tth_hire": json.dumps(payload["tth_hire"]),
        "tth_process": json.dumps(payload["tth_process"]),
        "dept_labels": json.dumps(payload["dept_labels"]),
        "dept_data": json.dumps(payload["dept_data"]),
        "conv_labels": json.dumps(payload["conv_labels"]),
        "conv_data": json.dumps(payload["conv_data"]),
        "dropoff_data": json.dumps(payload["dropoff_data"]),
        "trend_labels": json.dumps(payload["trend_labels"]),
        "trend_apps": json.dumps(payload["trend_apps"]),
        "trend_hired": json.dumps(payload["trend_hired"]),
        "heatmap": json.dumps(payload["heatmap"]),
        "kpi": payload["kpi"],
        "vacancy_rows": payload["vacancy_rows"],
        "department_efficiency": payload["department_efficiency"],
        "drilldown": payload["drilldown"],
        "filter_options": payload["filter_options"],
        "selected_filters": payload["selected_filters"],
        "export_query": export_query,
        "base_filter_query": base_filter_query,
        "funnel_status_pairs": FUNNEL_STAGES,
        "department_labels": payload["dept_labels"],
    }
    return render(request, "analytics/dashboard.html", context)


@analytics_required
def generate_report_excel(request):
    filters = parse_filters(request.GET)
    payload = build_dashboard_payload(filters)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Аналитика вакансий"

    ws.append(["Фильтры"])
    ws.append(["Период", _get_period_label(payload)])
    ws.append([
        "Отдел",
        _label_from_choice(payload["selected_filters"]["department"], payload["filter_options"]["departments"]),
    ])
    ws.append([
        "Статус вакансии",
        _label_from_choice(payload["selected_filters"]["vacancy_status"], payload["filter_options"]["vacancy_statuses"]),
    ])
    ws.append([
        "Статус отклика",
        _label_from_choice(payload["selected_filters"]["application_status"], payload["filter_options"]["application_statuses"]),
    ])
    ws.append([])
    ws.append(["KPI"])
    ws.append(["Вакансий в срезе", payload["kpi"]["total_vacancies"]])
    ws.append(["Кандидатов в срезе", payload["kpi"]["total_candidates"]])
    ws.append(["Нанято в срезе", payload["kpi"]["hired_this_period"]])
    ws.append(["Запланировано собеседований", payload["kpi"]["total_interviews_scheduled"]])
    ws.append([])

    headers = ["Вакансия", "Отдел", "Статус", "Откликов", "На интервью", "Нанято"]
    ws.append(headers)
    for v in payload["vacancy_rows"]:
        ws.append([
            v["title"],
            v["department"],
            v["status"],
            v["applications"],
            v["interviews"],
            v["hired"],
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="hr_report.xlsx"'
    return response


@analytics_required
def generate_report_pdf(request):
    filters = parse_filters(request.GET)
    payload = build_dashboard_payload(filters)
    fonts = _ensure_pdf_font()
    regular_font = fonts["regular"]
    bold_font = fonts["bold"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    title_style = ParagraphStyle(
        name="CyrTitle",
        fontName=bold_font,
        fontSize=20,
        leading=24,
        alignment=1,
        textColor=colors.HexColor("#1e293b"),
    )

    def make_table_style(header_bg_hex, font_size=10):
        return TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), regular_font),
            ("FONTNAME", (0, 0), (-1, 0), bold_font),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg_hex)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])

    elements = []
    elements.append(Paragraph("Аналитика вакансий", title_style))
    elements.append(Spacer(1, 10 * mm))

    filters_table = Table(
        [
            ["Фильтр", "Значение"],
            ["Период", _get_period_label(payload)],
            [
                "Отдел",
                _label_from_choice(payload["selected_filters"]["department"], payload["filter_options"]["departments"]),
            ],
            [
                "Статус вакансии",
                _label_from_choice(payload["selected_filters"]["vacancy_status"], payload["filter_options"]["vacancy_statuses"]),
            ],
            [
                "Статус отклика",
                _label_from_choice(payload["selected_filters"]["application_status"], payload["filter_options"]["application_statuses"]),
            ],
        ],
        colWidths=[60 * mm, 100 * mm],
    )
    filters_table.setStyle(make_table_style("#475569"))
    elements.append(filters_table)
    elements.append(Spacer(1, 8 * mm))

    kpi = payload["kpi"]
    kpi_data = [
        ["Метрика", "Значение"],
        ["Вакансий в срезе", str(kpi["total_vacancies"])],
        ["Кандидатов в срезе", str(kpi["total_candidates"])],
        ["Нанято в срезе", str(kpi["hired_this_period"])],
        ["Запланировано собеседований", str(kpi["total_interviews_scheduled"])],
    ]
    kpi_table = Table(kpi_data, colWidths=[120 * mm, 40 * mm])
    kpi_table.setStyle(make_table_style("#1e293b"))
    elements.append(kpi_table)
    elements.append(Spacer(1, 10 * mm))

    vacancy_data = [["Вакансия", "Отдел", "Статус", "Откликов", "Нанято"]]
    for v in payload["vacancy_rows"]:
        vacancy_data.append([
            v["title"], v["department"], v["status"], str(v["applications"]), str(v["hired"]),
        ])
    if len(vacancy_data) > 1:
        vac_table = Table(vacancy_data, colWidths=[45 * mm, 35 * mm, 30 * mm, 25 * mm, 25 * mm])
        vac_table.setStyle(make_table_style("#3b82f6", font_size=9))
        elements.append(vac_table)

    doc.build(elements)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="hr_report.pdf"'
    return response


def _label_from_choice(value, options):
    if isinstance(options, list) and options and isinstance(options[0], tuple):
        for option_value, option_label in options:
            if option_value == value:
                return option_label
    if isinstance(options, list) and options and isinstance(options[0], str):
        if value == "all":
            return "Все"
        if value in options:
            return value
        return value
    if value == "all":
        return "Все"
    return value


def _get_period_label(payload):
    period_map = dict(payload["filter_options"]["periods"])
    return period_map.get(payload["selected_filters"]["period"], "Последние 90 дней")
