"""Tatsächliche Größenlimits, Qualität, Pixelbasis und unmögliche Ziele prüfen."""
import hashlib
import io
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jpegdrop as jd
from PIL import Image


class TargetSizeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def noise(self, size=64):
        image = Image.frombytes('RGB', (size, size), random.Random(7).randbytes(size * size * 3))
        path = self.root/'source.png';image.save(path)
        return image, path

    def test_units_are_exact_and_invalid_sizes_are_rejected(self):
        for text, expected in [('0',0),('500KB',500000),('0,5 MB',500000),('1KiB',1024),
                               ('1.5MiB',1572864),('12B',12),('.25 KB',250)]:
            with self.subTest(text=text):self.assertEqual(jd.parse_target_size(text),expected)
        for text in ['-1KB','nan','1GB','0.1B','1e3','abc','1KB extra','0.'+'9'*40+'B']:
            with self.subTest(text=text),self.assertRaises(ValueError):jd.parse_target_size(text)

    def test_jpeg_uses_highest_actual_quality_that_fits(self):
        image, source = self.noise()
        encoded = {}
        for quality in range(85,14,-1):
            buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=quality,optimize=True,progressive=True)
            encoded[quality]=buffer.getvalue()
        target=len(encoded[60])
        expected=max(q for q,data in encoded.items() if len(data)<=target)
        before=source.read_bytes()
        result=jd.process_one(source,jd.CODECS['jpeg'],'hoch','-out',options=jd.ImageOptions(target_bytes=target))
        self.assertIsNone(result['error'],result)
        self.assertEqual(result['quality'],expected)
        self.assertEqual(Path(result['target']).read_bytes(),encoded[expected])
        self.assertLessEqual(result['out_bytes'],target)
        self.assertTrue(result['size_limit_met'])
        self.assertEqual(source.read_bytes(),before)

    def test_nonmonotone_sizes_and_mutating_encoder_do_not_change_pixel_basis(self):
        image=Image.new('RGB',(8,8),'red');reference=image.tobytes();seen=[]
        result=jd.empty_result(self.root/'source.png','jpeg','hoch')
        result.update(source_width=8,source_height=8)
        def encode(prepared,path,**kwargs):
            seen.append(prepared.tobytes());prepared.putpixel((0,0),(0,0,0))
            Path(path).write_bytes(b'x'*{85:1000,84:1200,83:700}[kwargs['quality']])
        with patch.object(Image.Image,'save',encode):
            jd.encode_with_limit(image,self.root/'candidate.jpg',jd.CODECS['jpeg'],'hoch',None,
                                 jd.ImageOptions(target_bytes=800),result,None)
        self.assertEqual(result['quality'],83)
        self.assertEqual(result['encoding_attempts'],3)
        self.assertEqual(seen,[reference]*3)

    def test_lossless_impossible_limit_leaves_no_output_or_temporary_files(self):
        _,source=self.noise()
        before=hashlib.sha256(source.read_bytes()).digest()
        for codec in ['png','webp']:
            with self.subTest(codec=codec):
                result=jd.process_one(source,jd.CODECS[codec],'verlustfrei','-out',
                    options=jd.ImageOptions(target_bytes=1,preserve_alpha=True))
                self.assertEqual(result['error_code'],'target_size_unreachable')
                self.assertIsNone(result['target'])
                self.assertFalse(result['size_limit_met'])
                self.assertEqual(result['encoding_attempts'],1)
                self.assertGreater(result['smallest_attempt_bytes'],1)
                self.assertEqual(list(self.root.glob('.jpegdrop-*')),[])
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)

    def test_extra_resize_is_explicit_bounded_and_respects_fixed_quality(self):
        image,source=self.noise(256)
        small=image.copy();small.thumbnail((64,64),Image.Resampling.LANCZOS)
        encoded=io.BytesIO();small.save(encoded,format='JPEG',quality=90,optimize=True,progressive=True)
        target=len(encoded.getvalue())+10
        result=jd.process_one_with_fixed_quality(source,90,'-out',options=jd.ImageOptions(target_bytes=target))
        self.assertEqual(result['error_code'],'target_size_unreachable')
        self.assertEqual(result['encoding_attempts'],1)
        result=jd.process_one_with_fixed_quality(source,90,'-out',options=jd.ImageOptions(
            target_bytes=target,allow_target_resize=True,minimum_target_edge=64))
        self.assertIsNone(result['error'],result)
        self.assertLessEqual(result['out_bytes'],target)
        self.assertEqual(result['quality'],90)
        self.assertGreaterEqual(max(result['output_width'],result['output_height']),64)
        self.assertLess(max(result['output_width'],result['output_height']),256)
        self.assertLessEqual(result['encoding_attempts'],4)

    def test_lossless_alpha_fits_without_pixel_or_metadata_loss(self):
        source=self.root/'alpha.png'
        image=Image.new('RGBA',(24,12),(20,40,60,64));image.save(source)
        for codec in ['png','webp']:
            with self.subTest(codec=codec):
                result=jd.process_one(source,jd.CODECS[codec],'verlustfrei','-out',
                                     options=jd.ImageOptions(target_bytes=1000,preserve_alpha=True))
                self.assertIsNone(result['error'],result)
                self.assertLessEqual(result['out_bytes'],1000)
                with Image.open(result['target']) as output:
                    self.assertEqual(output.convert('RGBA').tobytes(),image.tobytes())
                    self.assertFalse(output.getexif());self.assertFalse(output.info.get('icc_profile'))

    def test_lossy_avif_and_webp_respect_limit_and_keep_alpha(self):
        image,source=self.noise()
        image.putalpha(64);image.save(source)
        for codec,upper,extra in [('avif',70,{'speed':6}),('webp',75,{'method':6})]:
            with self.subTest(codec=codec):
                reference=io.BytesIO();image.save(reference,format=codec.upper(),quality=30,**extra)
                target=len(reference.getvalue())
                result=jd.process_one(source,jd.CODECS[codec],'hoch','-out',
                    options=jd.ImageOptions(target_bytes=target,preserve_alpha=True))
                self.assertIsNone(result['error'],result)
                self.assertLessEqual(result['out_bytes'],target)
                self.assertGreaterEqual(result['quality'],30);self.assertLessEqual(result['quality'],upper)
                with Image.open(result['target']) as output:
                    self.assertEqual(output.convert('RGBA').getchannel('A').getextrema(),(64,64))
                    self.assertFalse(output.getexif());self.assertFalse(output.info.get('icc_profile'))

    def test_one_pixel_minimum_and_no_upscale(self):
        source=self.root/'tiny.png';Image.new('RGB',(1,1),'red').save(source)
        result=jd.process_one(source,jd.CODECS['png'],'verlustfrei','-out',options=jd.ImageOptions(
            target_bytes=1000,allow_target_resize=True,minimum_target_edge=320))
        self.assertIsNone(result['error'],result)
        self.assertEqual((result['output_width'],result['output_height']),(1,1))
        result=jd.process_one(source,jd.CODECS['jpeg'],'hoch','-out',options=jd.ImageOptions(
            target_bytes=1,allow_target_resize=True,minimum_target_edge=1))
        self.assertEqual(result['error_code'],'target_size_unreachable')
        self.assertLessEqual(result['encoding_attempts'],81)
        self.assertEqual(list(self.root.glob('.jpegdrop-*')),[])


if __name__=='__main__':unittest.main()
