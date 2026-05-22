from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from analytics.services import (
    build_dashboard_payload,
    parse_filters,
)
from tests.factories import (
    DEFAULT_PASSWORD,
    create_application,
    create_interview,
    create_stage,
    create_user,
    create_vacancy,
)
from vacancies.models import Application, Interview, Vacancy


class AnalyticsMetricTests(TestCase):
    def setUp(self):
        self.hr = create_user("analytics_hr", role=User.Role.HR)
        self.director = create_user("analytics_director", role=User.Role.DIRECTOR)
        self.admin = create_user("analytics_admin", role=User.Role.ADMIN)
        self.candidate = create_user("analytics_candidate", role=User.Role.CANDIDATE)
        self.other_candidate = create_user("analytics_candidate_2", role=User.Role.CANDIDATE)

        self.open_it = create_vacancy(self.hr, title="Python Dev", status=Vacancy.Status.OPEN)
        self.open_sales = create_vacancy(self.hr, title="Sales", status=Vacancy.Status.OPEN)
        self.open_sales.department = "Sales"
        self.open_sales.save(update_fields=["department"])
        self.closed_vacancy = create_vacancy(self.hr, title="Closed", status=Vacancy.Status.CLOSED)

        stage = create_stage(self.open_it, order=1, title="Интервью")
        hired_app = create_application(self.open_it, self.candidate, status=Application.Status.HIRED)
        screening_app = create_application(
            self.open_sales, self.other_candidate, status=Application.Status.SCREENING
        )
        create_interview(
            hired_app,
            stage,
            status=Interview.Status.COMPLETED,
            scheduled_at=timezone.now() + timedelta(days=5),
        )
        create_interview(
            screening_app,
            create_stage(self.open_sales, order=1, title="Первый этап"),
            status=Interview.Status.SCHEDULED,
            scheduled_at=timezone.now() + timedelta(hours=2),
        )

    def test_funnel_data_returns_counts_by_status(self):
        payload = build_dashboard_payload(parse_filters({}))
        labels, counts = payload["funnel_labels"], payload["funnel_data"]
        self.assertEqual(labels[0], "Новый")
        self.assertEqual(len(labels), 6)
        self.assertEqual(sum(counts), Application.objects.count())

    def test_vacancies_by_department_includes_only_open(self):
        payload = build_dashboard_payload(parse_filters({"vacancy_status": "open"}))
        labels, counts = payload["dept_labels"], payload["dept_data"]
        result = dict(zip(labels, counts))
        self.assertEqual(result["IT"], 1)
        self.assertEqual(result["Sales"], 1)
        self.assertNotIn("Closed", result)

    def test_conversion_rates_use_cumulative_counts(self):
        payload = build_dashboard_payload(parse_filters({}))
        labels, values, dropoff = payload["conv_labels"], payload["conv_data"], payload["dropoff_data"]
        self.assertEqual(len(labels), 4)
        self.assertTrue(all(0 <= value <= 100 for value in values))
        self.assertTrue(all(0 <= value <= 100 for value in dropoff))

    def test_heatmap_data_counts_scheduled_and_completed_interviews(self):
        payload = build_dashboard_payload(parse_filters({}))
        grid = payload["heatmap"]
        total = sum(sum(row) for row in grid)
        self.assertEqual(total, 2)

    def test_kpi_cards_returns_expected_keys(self):
        payload = build_dashboard_payload(parse_filters({}))
        kpi = payload["kpi"]
        self.assertSetEqual(
            set(kpi.keys()),
            {"total_vacancies", "total_candidates", "hired_this_period", "total_interviews_scheduled"},
        )
        self.assertEqual(kpi["total_vacancies"], 3)

    def test_time_to_hire_returns_six_points(self):
        payload = build_dashboard_payload(parse_filters({}))
        labels, values = payload["tth_labels"], payload["tth_hire"]
        self.assertEqual(len(labels), 6)
        self.assertEqual(len(values), 6)

    def test_dashboard_payload_respects_department_filter(self):
        payload = build_dashboard_payload(parse_filters({"department": "Sales"}))
        self.assertEqual(payload["kpi"]["total_vacancies"], 1)
        self.assertEqual(payload["kpi"]["total_candidates"], 1)
        self.assertEqual(len(payload["vacancy_rows"]), 1)

    def test_dashboard_payload_builds_drilldown(self):
        payload = build_dashboard_payload(parse_filters({"drilldown": "status", "value": "screening"}))
        self.assertIsNotNone(payload["drilldown"])
        self.assertIn("Детализация по статусу", payload["drilldown"]["title"])
        self.assertGreaterEqual(len(payload["drilldown"]["rows"]), 1)


class AnalyticsViewTests(TestCase):
    def setUp(self):
        self.hr = create_user("analytics_view_hr", role=User.Role.HR)
        self.director = create_user("analytics_view_director", role=User.Role.DIRECTOR)
        self.admin = create_user("analytics_view_admin", role=User.Role.ADMIN)
        self.candidate = create_user("analytics_view_candidate", role=User.Role.CANDIDATE)
        create_vacancy(self.hr, title="Vacancy", status=Vacancy.Status.OPEN)

    def test_dashboard_forbidden_for_hr(self):
        self.client.login(username=self.hr.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_forbidden_for_candidate(self):
        self.client.login(username=self.candidate.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_available_for_director(self):
        self.client.login(username=self.director.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:dashboard"), {"period": "30", "department": "all"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Дашборд аналитики")
        self.assertContains(response, "Фильтры аналитики")

    def test_dashboard_available_for_admin(self):
        self.client.login(username=self.admin.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_excel_report_exports_file(self):
        self.client.login(username=self.director.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:report_excel"), {"period": "30", "department": "IT"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("hr_report.xlsx", response["Content-Disposition"])

    def test_pdf_report_exports_file(self):
        self.client.login(username=self.admin.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:report_pdf"), {"period": "30", "department": "IT"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("hr_report.pdf", response["Content-Disposition"])

    def test_drilldown_query_renders_modal(self):
        self.client.login(username=self.director.username, password=DEFAULT_PASSWORD)
        response = self.client.get(
            reverse("analytics:dashboard"),
            {"drilldown": "status", "value": "new"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "drilldownModal")
