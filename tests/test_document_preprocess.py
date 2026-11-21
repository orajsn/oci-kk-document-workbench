import hashlib
import io
import unittest
import importlib.util

from PIL import Image, ImageDraw

from kkpilot.adapters.document_preprocess import prepare_document_views
from kkpilot.adapters.errors import AdapterError
from kkpilot.validation import ValidationError


def fixture():
    img = Image.new('RGB', (800, 600), 'white')
    d = ImageDraw.Draw(img)
    for y in (150, 300, 450):
        d.rectangle((20, y, 65, y+20), fill='black')
        d.line((20, y+40, 780, y+40), fill='black', width=2)
    out = io.BytesIO()
    img.save(out, 'PNG')
    return out.getvalue()


class DocumentViewsTest(unittest.TestCase):
    def test_default_returns_exact_original(self):
        raw = fixture()
        views, meta = prepare_document_views(raw)
        self.assertEqual(views[0]['bytes'], raw)
        self.assertEqual(meta['original_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(meta['status'], 'DISABLED')
        self.assertEqual(prepare_document_views(raw, {'enabled': False})[0][0]['bytes'], raw)

    def test_crop_rotation_and_overlapping_full_width_views(self):
        raw = fixture()
        views, meta = prepare_document_views(raw, {'enabled': True, 'crop': [0, 0, 800, 600]})
        self.assertEqual([v['name'] for v in views], ['context', 'top', 'bottom'])
        self.assertEqual(views[1]['size'], [800, 360])
        self.assertEqual(views[2]['size'], [800, 360])
        top = Image.open(io.BytesIO(views[1]['bytes'])).convert('L')
        bottom = Image.open(io.BytesIO(views[2]['bytes'])).convert('L')
        # Row label at y=300 appears in both views, at the left edge of each.
        self.assertLess(top.getpixel((40, 310)), 40)
        self.assertLess(bottom.getpixel((40, 70)), 40)
        self.assertEqual(meta['transforms'][1]['bounds'], [0, 0, 800, 600])
        rotated, _ = prepare_document_views(raw, {'enabled': True, 'crop': [0, 0, 800, 600], 'rotate': 90})
        self.assertEqual(rotated[0]['size'], [600, 800])

    def test_partial_manual_crop_excludes_other_panels_in_all_views(self):
        img = Image.new('RGB', (1000, 600), (255, 0, 0))
        ImageDraw.Draw(img).rectangle((200, 100, 799, 499), fill=(240, 240, 240))
        out = io.BytesIO()
        img.save(out, 'PNG')
        views, meta = prepare_document_views(out.getvalue(), {
            'enabled': True, 'crop': [200, 100, 800, 500], 'rotate': 90,
            'auto_crop': True})
        self.assertEqual(meta['status'], 'READY')
        self.assertEqual(views[0]['size'], [400, 600])
        for view in views:
            image = Image.open(io.BytesIO(view['bytes'])).convert('RGB')
            for x, y in ((0, 0), (image.width-1, 0), (0, image.height-1),
                         (image.width-1, image.height-1), (image.width//2, image.height//2)):
                r, g, b = image.getpixel((x, y))
                self.assertLess(abs(r-g), 10)
                self.assertLess(abs(r-b), 10)

    def test_invalid_policy_is_rejected_even_when_disabled(self):
        raw = fixture()
        for policy in (False, 0, [], {'unknown': True}, {'enabled': 1},
                       {'auto_crop': 1}, {'table_views': 'yes'},
                       {'rotate': False}, {'max_edge': True}, {'crop': [0, 0, True, 4]}):
            with self.subTest(policy=policy), self.assertRaises(AdapterError):
                prepare_document_views(raw, policy)

    def test_manual_bounds_and_pixel_cap(self):
        with self.assertRaises(AdapterError):
            prepare_document_views(fixture(), {'enabled': True, 'crop': [0, 0, 801, 600]})
        img = Image.new('RGB', (5000, 4001), 'white')
        out = io.BytesIO()
        img.save(out, 'PNG')
        with self.assertRaises(ValidationError):
            prepare_document_views(out.getvalue(), {'enabled': True})

    def test_auto_crop_is_review_or_unavailable_on_blank(self):
        img = Image.new('RGB', (800, 600), 'white')
        out = io.BytesIO()
        img.save(out, 'PNG')
        views, meta = prepare_document_views(out.getvalue(), {'enabled': True, 'auto_crop': True})
        self.assertIn(meta['status'], ('NEEDS_REVIEW', 'UNAVAILABLE'))
        self.assertEqual(views[0]['bytes'], out.getvalue())

    def test_exif_orientation_precedes_manual_crop(self):
        img = Image.new('RGB', (400, 300), 'white')
        exif = Image.Exif()
        exif[274] = 6
        out = io.BytesIO()
        img.save(out, 'JPEG', exif=exif)
        views, meta = prepare_document_views(out.getvalue(), {'enabled': True, 'crop': [0, 0, 300, 400]})
        self.assertEqual(meta['transforms'][0]['size'], [300, 400])
        self.assertEqual(views[0]['size'], [300, 400])

    @unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'), 'OpenCV/numpy unavailable')
    def test_synthetic_page_collage_and_touching_edge(self):
        def png(polygons, marks=()):
            img = Image.new('RGB', (1000, 800), '#333333')
            draw = ImageDraw.Draw(img)
            for points in polygons:
                draw.polygon(points, fill='white')
            for x, y in marks:
                draw.ellipse((x-10, y-10, x+10, y+10), fill='black')
            out = io.BytesIO()
            img.save(out, 'PNG')
            return out.getvalue()
        page = png([[(160, 140), (790, 90), (850, 670), (110, 700)]],
                   [(210, 190), (745, 155), (795, 615), (175, 640)])
        views, meta = prepare_document_views(page, {'enabled': True, 'auto_crop': True})
        self.assertEqual(meta['status'], 'READY')
        self.assertTrue(any(t['type'] == 'perspective' for t in meta['transforms']))
        document = Image.open(io.BytesIO(next(v['bytes'] for v in views if v['name'] == 'document'))).convert('L')
        # Four independent near-corner marks survive correction inside the page.
        w, h = document.size
        for box in ((0, 0, w//4, h//4), (3*w//4, 0, w, h//4),
                    (0, 3*h//4, w//4, h), (3*w//4, 3*h//4, w, h)):
            self.assertGreater(sum(count for value, count in enumerate(document.crop(box).histogram()) if value < 40), 30)
        collage = png([[(40, 80), (460, 80), (460, 720), (40, 720)],
                       [(540, 80), (960, 80), (960, 720), (540, 720)]])
        views, meta = prepare_document_views(collage, {'enabled': True, 'auto_crop': True})
        self.assertEqual(meta['status'], 'NEEDS_REVIEW')
        self.assertEqual(views[0]['bytes'], collage)
        touching = png([[(0, 50), (900, 50), (900, 740), (0, 740)]])
        views, meta = prepare_document_views(touching, {'enabled': True, 'auto_crop': True})
        self.assertEqual(meta['status'], 'NEEDS_REVIEW')
        self.assertEqual(views[0]['bytes'], touching)


if __name__ == '__main__':
    unittest.main()
