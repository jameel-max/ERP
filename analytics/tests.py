from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from core.models import User, SystemSetting
from operations.models import Trader, Crop
from finance.models import TreasuryAccount, ExpenseCategory, Expense
from finance.services import FinanceService


class Phase3AnalyticsTests(TestCase):
    """
    اختبارات لوحة المدير والتقارير (المرحلة الثالثة).
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.manager = User.objects.create_user(
            username='an_manager', password='pass', role=User.Role.MANAGER
        )
        self.accountant = User.objects.create_user(
            username='an_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.trader = Trader.objects.create(code='T001', name='ابو معتز القيسي')
        self.crop = Crop.objects.create(name='نكتارين')
        self.cash = TreasuryAccount.objects.create(
            name='الصندوق', account_type=TreasuryAccount.AccountType.CASH,
            current_balance=Decimal('500.000'),
        )
        self.category = ExpenseCategory.objects.create(name='محروقات')

    def test_daily_report_pages_render_for_manager(self):
        """
        التقرير اليومي (HTML و PDF) ولوحة المؤشرات تفتح للمدير وتعيد محتوى صحيحاً.
        """
        FinanceService.record_expense(
            category=self.category, amount=Decimal('25.000'),
            due_date=timezone.now().date(), status=Expense.Status.PAID,
            treasury_account=self.cash, description='وقود', user=self.accountant,
        )
        client = Client()
        client.force_login(self.manager)

        html = client.get(reverse('manager_daily_report'))
        self.assertEqual(html.status_code, 200)
        self.assertContains(html, 'التقرير اليومي')

        pdf = client.get(reverse('manager_daily_pdf'))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertGreater(len(pdf.content), 1000)

        excel = client.get(reverse('export_manager_daily_excel'))
        self.assertEqual(excel.status_code, 200)
        self.assertGreater(len(excel.content), 1000)

        dashboard = client.get(reverse('analytics_dashboard'))
        self.assertEqual(dashboard.status_code, 200)

    def test_daily_report_requires_manager_role(self):
        """
        التقرير اليومي ولوحة المؤشرات محصورة بصلاحية المدير.
        """
        client = Client()
        client.force_login(self.accountant)
        self.assertEqual(client.get(reverse('manager_daily_report')).status_code, 403)
        self.assertEqual(client.get(reverse('manager_daily_pdf')).status_code, 403)
        self.assertEqual(client.get(reverse('analytics_dashboard')).status_code, 403)
