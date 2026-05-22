from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from analytics.views import (
    _conversion_rates,
    _funnel_data,
    _heatmap_data,
    _kpi_cards,
    _time_to_hire,
    _vacancies_by_department,
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
        labels, counts = _funnel_data()
        self.assertEqual(labels[0], "Новый")
        self.assertEqual(len(labels), 6)
        self.assertEqual(sum(counts), Application.objects.count())

    def test_vacancies_by_department_includes_only_open(self):
        labels, counts = _vacancies_by_department()
        result = dict(zip(labels, counts))
        self.assertEqual(result["IT"], 1)
        self.assertEqual(result["Sales"], 1)
        self.assertNotIn("Closed", labels)

    def test_conversion_rates_returns_percentages(self):
        labels, values = _conversion_rates()
        self.assertEqual(len(labels), 5)
        self.assertTrue(all(value >= 0 for value in values))

    def test_heatmap_data_counts_scheduled_and_completed_interviews(self):
        grid = _heatmap_data()
        total = sum(sum(row) for row in grid)
        self.assertEqual(total, 2)

    def test_kpi_cards_returns_expected_keys(self):
        kpi = _kpi_cards()
        self.assertSetEqual(
            set(kpi.keys()),
            {"total_vacancies", "total_candidates", "hired_this_month", "total_interviews_scheduled"},
        )
        self.assertEqual(kpi["total_vacancies"], 2)

    def test_time_to_hire_returns_six_points(self):
        labels, values = _time_to_hire()
        self.assertEqual(len(labels), 6)
        self.assertEqual(len(values), 6)


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
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Дашборд аналитики")

    def test_dashboard_available_for_admin(self):
        self.client.login(username=self.admin.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_excel_report_exports_file(self):
        self.client.login(username=self.director.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:report_excel"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("hr_report.xlsx", response["Content-Disposition"])

    def test_pdf_report_exports_file(self):
        self.client.login(username=self.admin.username, password=DEFAULT_PASSWORD)
        response = self.client.get(reverse("analytics:report_pdf"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("hr_report.pdf", response["Content-Disposition"])
