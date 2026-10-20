from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal

from core.models import User, SystemSetting
from operations.models import PackagingGroup, BoxType


class PackagingResourcesTests(TestCase):
    """
    القسم الأول: موارد التغليف (مجموعات مستقلة + أصنافها).
    """

    def setUp(self):
        self.settings = SystemSetting.get_settings()
        self.settings.save()
        self.accountant = User.objects.create_user(
            username='pkg_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.client = Client()
        self.client.force_login(self.accountant)

    def test_create_group_then_add_item(self):
        resp = self.client.post(reverse('packaging_group_create'), {
            'name': 'الصناديق',
            'notes': 'كل أنواع الصناديق',
            'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        group = PackagingGroup.objects.get(name='الصناديق')

        resp = self.client.post(reverse('packaging_item_create', args=[group.pk]), {
            'group': group.pk,
            'name': 'صندوق أزرق',
            'size': '8KG',
            'color_or_type': 'أزرق',
            'tare_weight': '0.800',
            'discount_price': '1.500',
            'company_stock': '100',
            'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        item = BoxType.objects.get(name='صندوق أزرق')
        self.assertEqual(item.group_id, group.pk)
        self.assertEqual(item.size, '8KG')

    def test_groups_are_independent(self):
        boxes_group = PackagingGroup.objects.create(name='الصناديق')
        paper_group = PackagingGroup.objects.create(name='ورق التغليف')
        BoxType.objects.create(
            name='صندوق', size='5KG', color_or_type='أزرق',
            group=boxes_group,
        )
        BoxType.objects.create(
            name='ورق شفاف', size='40×30', color_or_type='شفاف',
            group=paper_group,
        )
        self.assertEqual(boxes_group.items.count(), 1)
        self.assertEqual(paper_group.items.count(), 1)
        self.assertNotEqual(
            boxes_group.items.first().pk, paper_group.items.first().pk
        )

    def test_page_lists_groups_and_items(self):
        group = PackagingGroup.objects.create(name='ورق التغليف')
        BoxType.objects.create(
            name='ورق شفاف', size='40×30', color_or_type='شفاف', group=group,
        )
        resp = self.client.get(reverse('packaging_resources'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ورق التغليف')
        self.assertContains(resp, 'ورق شفاف')
        self.assertContains(resp, '40×30')

    def test_viewer_cannot_create_group(self):
        viewer = User.objects.create_user(
            username='pkg_viewer', password='pass', role=User.Role.VIEWER
        )
        self.client.force_login(viewer)
        resp = self.client.post(reverse('packaging_group_create'), {
            'name': 'مجموعة ممنوعة',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(PackagingGroup.objects.filter(name='مجموعة ممنوعة').exists())

    def test_reception_box_type_comes_from_farm_boxes_only(self):
        """حقل «نوع الصناديق» في الاستلام يسحب من صناديق المزرعة، لا من صناديق الشركة."""
        from operations.forms import TruckReceiptForm
        from boxes.models import FarmBoxType

        farm_box = FarmBoxType.objects.create(
            name='صندوق مزرعة', description='بلاستيك', tare_weight=Decimal('0.600'),
        )
        company_box = BoxType.objects.create(
            name='صندوق شركة', color_or_type='أسود', is_box=True,
        )
        material = BoxType.objects.create(
            name='ورق تغليف', color_or_type='شفاف', is_box=False,
        )

        form = TruckReceiptForm()
        queryset = form.fields['box_type'].queryset
        self.assertIn(farm_box, queryset)
        self.assertNotIn(company_box, queryset)
        self.assertNotIn(material, queryset)

        # «is_box» ما زال يفصل صناديق الشركة عن مواد التغليف في التوضيب
        packing_boxes = BoxType.objects.filter(is_active=True, is_box=True)
        self.assertIn(company_box, packing_boxes)
        self.assertNotIn(material, packing_boxes)
