import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import Attachment, User, AuditLog
from operations.models import Trader


def _image_bytes(color=(255, 0, 0), size=(40, 40)):
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='JPEG')
    return buffer.getvalue()


def _uploaded_image(name='photo.jpg', color=(0, 128, 0)):
    return SimpleUploadedFile(name, _image_bytes(color), content_type='image/jpeg')


class AttachmentTests(TestCase):
    """اختبارات نظام إرفاق الصور العام (عدة صور لكل سجل)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp(prefix='erp-attach-')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.media_override = override_settings(MEDIA_ROOT=self._media)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        self.accountant = User.objects.create_user(
            username='attach_accountant', password='pass', role=User.Role.ACCOUNTANT
        )
        self.viewer = User.objects.create_user(
            username='attach_viewer', password='pass', role=User.Role.VIEWER
        )
        self.trader = Trader.objects.create(name='تاجر صور', code='IMG-1')

    def _upload_url(self):
        return reverse('attachments_upload', args=['operations', 'trader', self.trader.pk])

    def test_upload_multiple_images_at_once(self):
        self.client.force_login(self.accountant)
        response = self.client.post(self._upload_url(), {
            'images': [_uploaded_image('a.jpg', (255, 0, 0)),
                       _uploaded_image('b.jpg', (0, 0, 255)),
                       _uploaded_image('c.jpg', (0, 255, 0))],
            'caption': 'صور البضاعة',
            'next': reverse('trader_detail', args=[self.trader.pk]),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Attachment.objects.filter(object_id=self.trader.pk).count(), 3
        )
        for att in Attachment.objects.all():
            att.image.open('rb')
            self.assertTrue(att.image.read())
            att.image.close()
        self.assertTrue(AuditLog.objects.filter(action='UPLOAD_ATTACHMENTS').exists())

    def test_viewer_cannot_upload(self):
        self.client.force_login(self.viewer)
        self.client.post(self._upload_url(), {'images': [_uploaded_image()]})
        self.assertEqual(Attachment.objects.count(), 0)

    def test_invalid_extension_is_rejected(self):
        self.client.force_login(self.accountant)
        bad = SimpleUploadedFile('notes.txt', b'not an image', content_type='text/plain')
        self.client.post(self._upload_url(), {'images': [bad]})
        self.assertEqual(Attachment.objects.count(), 0)

    def test_delete_removes_record_and_file(self):
        self.client.force_login(self.accountant)
        self.client.post(self._upload_url(), {'images': [_uploaded_image()]})
        att = Attachment.objects.get()
        stored_name = att.image.name

        response = self.client.post(
            reverse('attachment_delete', args=[att.pk]),
            {'next': reverse('trader_detail', args=[self.trader.pk])},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertFalse(default_storage.exists(stored_name))

    def test_non_attachable_model_is_refused(self):
        self.client.force_login(self.accountant)
        url = reverse('attachments_upload', args=['core', 'notification', 1])
        response = self.client.post(url, {'images': [_uploaded_image()]})
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(response.status_code, 302)

    def test_gallery_renders_on_detail_page(self):
        self.client.force_login(self.accountant)
        response = self.client.get(reverse('trader_detail', args=[self.trader.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'الصور المرفقة')
