import calendar
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, Max
from django.utils import timezone

from vacancies.models import Application, Interview, Vacancy


FUNNEL_STAGES = [
    (Application.Status.NEW, "Новый"),
    (Application.Status.SCREENING, "Скрининг"),
    (Application.Status.INTERVIEW, "Собеседование"),
    (Application.Status.OFFER, "Оффер"),
    (Application.Status.HIRED, "Нанят"),
    (Application.Status.REJECTED, "Отклонён"),
]

PIPELINE_STAGES = [
    (Application.Status.NEW, "Новый"),
    (Application.Status.SCREENING, "Скрининг"),
    (Application.Status.INTERVIEW, "Собеседование"),
    (Application.Status.OFFER, "Оффер"),
    (Application.Status.HIRED, "Нанят"),
]

PERIOD_OPTIONS = [
    ("30", "Последние 30 дней"),
    ("90", "Последние 90 дней"),
    ("180", "Последние 180 дней"),
    ("365", "Последний год"),
    ("all", "За всё время"),
]


@dataclass
class AnalyticsFilters:
    period: str = "90"
    department: str = "all"
    vacancy_status: str = "all"
    vacancy_id: str = "all"
    application_status: str = "all"
    drilldown: str = ""
    value: str = ""

    @property
    def period_start(self):
        if self.period == "all":
            return None
        return timezone.now() - timedelta(days=int(self.period))


def parse_filters(query_params):
    period = query_params.get("period", "90")
    if period not in {item[0] for item in PERIOD_OPTIONS}:
        period = "90"

    department = query_params.get("department", "all")
    vacancy_status = query_params.get("vacancy_status", "all")
    vacancy_id = query_params.get("vacancy_id", "all")
    application_status = query_params.get("application_status", "all")
    drilldown = query_params.get("drilldown", "")
    value = query_params.get("value", "")

    valid_vacancy_status = {choice[0] for choice in Vacancy.Status.choices}
    if vacancy_status not in valid_vacancy_status:
        vacancy_status = "all"

    valid_application_status = {choice[0] for choice in Application.Status.choices}
    if application_status not in valid_application_status:
        application_status = "all"

    if vacancy_id != "all" and not str(vacancy_id).isdigit():
        vacancy_id = "all"

    return AnalyticsFilters(
        period=period,
        department=department or "all",
        vacancy_status=vacancy_status,
        vacancy_id=str(vacancy_id),
        application_status=application_status,
        drilldown=drilldown,
        value=value,
    )


def _safe_percent(part, whole):
    if not whole:
        return 0
    return round((part / whole) * 100, 1)


def _month_start(dt):
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(dt, months):
    year = dt.year
    month = dt.month + months
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _apply_vacancy_filters(queryset, filters):
    qs = queryset
    if filters.department != "all":
        qs = qs.filter(department=filters.department)
    if filters.vacancy_status != "all":
        qs = qs.filter(status=filters.vacancy_status)
    if filters.vacancy_id != "all":
        qs = qs.filter(id=int(filters.vacancy_id))
    if filters.period_start:
        qs = qs.filter(created_at__gte=filters.period_start)
    return qs


def _apply_application_filters(queryset, filters):
    qs = queryset
    if filters.department != "all":
        qs = qs.filter(vacancy__department=filters.department)
    if filters.vacancy_status != "all":
        qs = qs.filter(vacancy__status=filters.vacancy_status)
    if filters.vacancy_id != "all":
        qs = qs.filter(vacancy_id=int(filters.vacancy_id))
    if filters.application_status != "all":
        qs = qs.filter(status=filters.application_status)
    if filters.period_start:
        qs = qs.filter(applied_at__gte=filters.period_start)
    return qs


def _apply_interview_filters(queryset, filters):
    qs = queryset
    if filters.department != "all":
        qs = qs.filter(application__vacancy__department=filters.department)
    if filters.vacancy_status != "all":
        qs = qs.filter(application__vacancy__status=filters.vacancy_status)
    if filters.vacancy_id != "all":
        qs = qs.filter(application__vacancy_id=int(filters.vacancy_id))
    if filters.application_status != "all":
        qs = qs.filter(application__status=filters.application_status)
    if filters.period_start:
        qs = qs.filter(scheduled_at__gte=filters.period_start)
    return qs


def _build_funnel_data(applications_qs):
    """Distribution of applications by current status (snapshot)."""
    by_status = dict(applications_qs.values_list("status").annotate(total=Count("id")))
    labels = [label for _, label in FUNNEL_STAGES]
    counts = [by_status.get(status, 0) for status, _ in FUNNEL_STAGES]
    return labels, counts


def _build_pipeline_rates(applications_qs):
    """Stage transitions on cumulative reach counts.

    Cumulative reach[stage] = applications currently at this stage or any later stage in
    the pipeline. Rejected applications are excluded because we don't know at which stage
    they dropped off. This gives meaningful step-to-step conversion and drop-off ratios.
    """
    by_status = dict(applications_qs.values_list("status").annotate(total=Count("id")))
    pipeline_codes = [status for status, _ in PIPELINE_STAGES]
    cumulative = {}
    for idx, status in enumerate(pipeline_codes):
        later_statuses = pipeline_codes[idx:]
        cumulative[status] = sum(by_status.get(s, 0) for s in later_statuses)

    labels = []
    conversion = []
    dropoff = []
    for idx in range(len(PIPELINE_STAGES) - 1):
        current_status, current_label = PIPELINE_STAGES[idx]
        next_status, next_label = PIPELINE_STAGES[idx + 1]
        current_count = cumulative[current_status]
        next_count = cumulative[next_status]
        labels.append(f"{current_label} → {next_label}")
        conversion.append(_safe_percent(next_count, current_count))
        dropoff.append(_safe_percent(max(current_count - next_count, 0), current_count))
    return labels, conversion, dropoff


def _build_monthly_time_metrics(applications_qs):
    now = timezone.now()
    labels = []
    hire_values = []
    process_values = []

    for step in range(5, -1, -1):
        start_month = _month_start(_add_months(now, -step))
        end_month = _month_start(_add_months(start_month, 1))
        labels.append(start_month.strftime("%b %Y"))

        month_apps = applications_qs.filter(applied_at__gte=start_month, applied_at__lt=end_month)
        hired_apps = month_apps.filter(status=Application.Status.HIRED)
        completed_map = dict(
            Interview.objects.filter(
                application_id__in=hired_apps.values_list("id", flat=True),
                status=Interview.Status.COMPLETED,
            )
            .values("application_id")
            .annotate(last_dt=Max("scheduled_at"))
            .values_list("application_id", "last_dt")
        )

        process_map = dict(
            Interview.objects.filter(application_id__in=month_apps.values_list("id", flat=True))
            .values("application_id")
            .annotate(last_dt=Max("scheduled_at"))
            .values_list("application_id", "last_dt")
        )

        hire_durations = []
        for app in hired_apps:
            last_dt = completed_map.get(app.id)
            if last_dt:
                hire_durations.append(max((last_dt - app.applied_at).days, 0))

        process_durations = []
        for app in month_apps:
            last_dt = process_map.get(app.id)
            if last_dt:
                process_durations.append(max((last_dt - app.applied_at).days, 0))

        hire_values.append(round(sum(hire_durations) / len(hire_durations), 1) if hire_durations else 0)
        process_values.append(round(sum(process_durations) / len(process_durations), 1) if process_durations else 0)

    return labels, hire_values, process_values


def _build_hiring_trend(applications_qs):
    now = timezone.now()
    labels = []
    applications_data = []
    hired_data = []

    for idx in range(7, -1, -1):
        period_end = now - timedelta(days=idx * 7)
        period_start = period_end - timedelta(days=7)
        labels.append(period_end.strftime("%d.%m"))
        bucket = applications_qs.filter(applied_at__gte=period_start, applied_at__lt=period_end)
        applications_data.append(bucket.count())
        hired_data.append(bucket.filter(status=Application.Status.HIRED).count())

    return labels, applications_data, hired_data


def _build_department_distribution(vacancies_qs):
    rows = list(vacancies_qs.values("department").annotate(total=Count("id")).order_by("-total", "department"))
    labels = [row["department"] for row in rows]
    counts = [row["total"] for row in rows]
    return labels, counts


def _build_heatmap(interviews_qs):
    grid = [[0] * 24 for _ in range(7)]
    for interview in interviews_qs.filter(
        status__in=[Interview.Status.SCHEDULED, Interview.Status.COMPLETED, Interview.Status.CANCELLED]
    ):
        local_dt = timezone.localtime(interview.scheduled_at)
        grid[local_dt.weekday()][local_dt.hour] += 1
    return grid


def _build_department_efficiency(vacancies_qs, applications_qs, interviews_qs):
    result = []
    departments = (
        vacancies_qs.exclude(department="")
        .values_list("department", flat=True)
        .distinct()
        .order_by("department")
    )
    for department in departments:
        dep_vacancies = vacancies_qs.filter(department=department)
        dep_apps = applications_qs.filter(vacancy__department=department)
        dep_interviews = interviews_qs.filter(application__vacancy__department=department)
        hired_count = dep_apps.filter(status=Application.Status.HIRED).count()
        app_count = dep_apps.count()
        result.append(
            {
                "department": department,
                "vacancies": dep_vacancies.count(),
                "applications": app_count,
                "interviews": dep_interviews.count(),
                "hired": hired_count,
                "hire_rate": _safe_percent(hired_count, app_count),
            }
        )
    return result


def _build_vacancy_rows_with_filters(vacancies_qs, applications_qs):
    rows = []
    # Build counts with separate ORM aggregations to stay compatible across Django versions.
    interview_counts = dict(
        applications_qs.filter(status=Application.Status.INTERVIEW)
        .values_list("vacancy_id")
        .annotate(total=Count("id"))
    )
    hired_counts = dict(
        applications_qs.filter(status=Application.Status.HIRED)
        .values_list("vacancy_id")
        .annotate(total=Count("id"))
    )
    total_counts = dict(applications_qs.values_list("vacancy_id").annotate(total=Count("id")))

    for vacancy in vacancies_qs.order_by("title"):
        rows.append(
            {
                "id": vacancy.id,
                "title": vacancy.title,
                "department": vacancy.department,
                "status": vacancy.get_status_display(),
                "applications": total_counts.get(vacancy.id, 0),
                "interviews": interview_counts.get(vacancy.id, 0),
                "hired": hired_counts.get(vacancy.id, 0),
            }
        )
    return rows


def _build_drilldown(filters, vacancies_qs, applications_qs):
    if not filters.drilldown:
        return None

    if filters.drilldown == "status":
        valid = {status for status, _ in FUNNEL_STAGES}
        if filters.value not in valid:
            return None
        rows = []
        data = (
            applications_qs.filter(status=filters.value)
            .select_related("candidate", "vacancy")
            .order_by("-applied_at")[:50]
        )
        for item in data:
            rows.append([
                str(item.candidate),
                item.vacancy.title,
                item.vacancy.department,
                item.get_status_display(),
                timezone.localtime(item.applied_at).strftime("%d.%m.%Y %H:%M"),
            ])
        return {
            "title": f"Детализация по статусу: {dict(FUNNEL_STAGES).get(filters.value, filters.value)}",
            "columns": ["Кандидат", "Вакансия", "Отдел", "Статус", "Дата отклика"],
            "rows": rows,
        }

    if filters.drilldown == "department":
        rows = []
        scoped_vacancies = vacancies_qs.filter(department=filters.value)
        scoped_apps = applications_qs.filter(vacancy__department=filters.value)
        for row in _build_vacancy_rows_with_filters(scoped_vacancies, scoped_apps)[:50]:
            rows.append([row["title"], row["status"], row["applications"], row["hired"]])
        return {
            "title": f"Детализация по отделу: {filters.value}",
            "columns": ["Вакансия", "Статус", "Откликов", "Нанято"],
            "rows": rows,
        }

    return None


def get_filter_options(filters):
    vacancies_for_filters = Vacancy.objects.all()
    if filters.department != "all":
        vacancies_for_filters = vacancies_for_filters.filter(department=filters.department)
    if filters.vacancy_status != "all":
        vacancies_for_filters = vacancies_for_filters.filter(status=filters.vacancy_status)

    return {
        "periods": PERIOD_OPTIONS,
        "departments": sorted(Vacancy.objects.values_list("department", flat=True).distinct()),
        "vacancy_statuses": [("all", "Все")] + list(Vacancy.Status.choices),
        "application_statuses": [("all", "Все")] + list(Application.Status.choices),
        "vacancies": list(vacancies_for_filters.values("id", "title").order_by("title")),
    }


def build_dashboard_payload(filters):
    vacancies_qs = _apply_vacancy_filters(Vacancy.objects.all(), filters)
    applications_qs = _apply_application_filters(Application.objects.all(), filters)
    interviews_qs = _apply_interview_filters(Interview.objects.all(), filters)

    funnel_labels, funnel_data = _build_funnel_data(applications_qs)
    conv_labels, conv_data, dropoff_data = _build_pipeline_rates(applications_qs)
    tth_labels, tth_hire, tth_process = _build_monthly_time_metrics(applications_qs)
    trend_labels, trend_apps, trend_hired = _build_hiring_trend(applications_qs)
    dept_labels, dept_data = _build_department_distribution(vacancies_qs)
    heatmap = _build_heatmap(interviews_qs)
    vacancy_rows = _build_vacancy_rows_with_filters(vacancies_qs, applications_qs)
    department_efficiency = _build_department_efficiency(vacancies_qs, applications_qs, interviews_qs)

    kpi = {
        "total_vacancies": vacancies_qs.count(),
        "total_candidates": applications_qs.values("candidate_id").distinct().count(),
        "hired_this_period": applications_qs.filter(status=Application.Status.HIRED).count(),
        "total_interviews_scheduled": interviews_qs.filter(status=Interview.Status.SCHEDULED).count(),
    }

    return {
        "kpi": kpi,
        "funnel_labels": funnel_labels,
        "funnel_data": funnel_data,
        "tth_labels": tth_labels,
        "tth_hire": tth_hire,
        "tth_process": tth_process,
        "dept_labels": dept_labels,
        "dept_data": dept_data,
        "conv_labels": conv_labels,
        "conv_data": conv_data,
        "dropoff_data": dropoff_data,
        "trend_labels": trend_labels,
        "trend_apps": trend_apps,
        "trend_hired": trend_hired,
        "heatmap": heatmap,
        "vacancy_rows": vacancy_rows,
        "department_efficiency": department_efficiency,
        "drilldown": _build_drilldown(filters, vacancies_qs, applications_qs),
        "filter_options": get_filter_options(filters),
        "selected_filters": {
            "period": filters.period,
            "department": filters.department,
            "vacancy_status": filters.vacancy_status,
            "vacancy_id": filters.vacancy_id,
            "application_status": filters.application_status,
        },
    }
