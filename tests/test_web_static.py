# -*- coding: utf-8 -*-

import mimetypes
import unittest

from miyouqian.service import web


class WebStaticAssetsTest(unittest.TestCase):
    def test_webp_avatar_is_served_as_an_image(self):
        self.assertEqual(mimetypes.guess_type("paimon-avatar-192.webp")[0], "image/webp")

    def test_account_avatar_file_exists(self):
        self.assertTrue((web.WEB_ROOT / "assets" / "paimon-avatar-192.webp").is_file())


if __name__ == "__main__":
    unittest.main()
