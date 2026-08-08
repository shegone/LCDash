import unittest

from scripts.sync_mindshare_public_site import _approved_pdf_url, _safe_pdf_name


class MindsharePublicSiteSyncTests(unittest.TestCase):
    def test_allows_public_same_site_upload_pdf(self):
        self.assertEqual(
            _approved_pdf_url(
                "https://css-mindshare.com/downloads/",
                "/wp-content/uploads/2024/12/Overview_20241010.pdf",
            ),
            "https://css-mindshare.com/wp-content/uploads/2024/12/Overview_20241010.pdf",
        )

    def test_rejects_external_private_and_non_pdf_links(self):
        self.assertIsNone(
            _approved_pdf_url(
                "https://css-mindshare.com/downloads/",
                "https://example.com/wp-content/uploads/private.pdf",
            )
        )
        self.assertIsNone(
            _approved_pdf_url(
                "https://css-mindshare.com/downloads/",
                "https://support.css-mindshare.com/private.pdf",
            )
        )
        self.assertIsNone(
            _approved_pdf_url(
                "https://css-mindshare.com/downloads/",
                "/wp-content/uploads/2024/12/product.docx",
            )
        )

    def test_pdf_filename_is_generated_and_safe(self):
        self.assertEqual(
            _safe_pdf_name(
                "https://css-mindshare.com/wp-content/uploads/2024/12/Product%20Overview.pdf"
            ),
            "css_mindshare_public_Product_20Overview.pdf",
        )


if __name__ == "__main__":
    unittest.main()
