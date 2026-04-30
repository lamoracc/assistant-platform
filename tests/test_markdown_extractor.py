import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.services.chunking import chunk_by_headings_and_paragraphs
from app.services.document_extractors import extract_markdown


SAMPLE = """---
topic_id: package_config
title: Package Configuration
relative_path: pms/packages/package_configuration.md
section_group: PMS Operations
breadcrumbs:
  - Welcome
  - PMS Operations
  - Packages
---

![](122.gif)
![](printer_icon.jpg)

[Welcome] > [PMS Operations] > [Packages]

| Previous | Next |
| --- | --- |
| [Rate Codes](rates.md) | [Package Groups](groups.md) |

# Configure Packages

Use Package Configuration to define packages for OPERA PMS.

1. Select Configuration.
2. Select Packages.
3. Click New.

| Field | Description |
| --- | --- |
| Package Code | Unique package code. |
| Description | Package description. |
"""


class MarkdownExtractorTests(unittest.TestCase):
    def test_front_matter_is_metadata_not_text(self) -> None:
        extracted = extract_markdown(SAMPLE.encode("utf-8"), "package_configuration.md")

        self.assertEqual(extracted.metadata["topic_id"], "package_config")
        self.assertEqual(extracted.metadata["title"], "Package Configuration")
        self.assertEqual(
            extracted.metadata["breadcrumbs"],
            ["Welcome", "PMS Operations", "Packages"],
        )
        self.assertNotIn("topic_id:", extracted.text)
        self.assertNotIn("relative_path:", extracted.text)

    def test_navigation_images_and_breadcrumbs_are_removed(self) -> None:
        extracted = extract_markdown(SAMPLE.encode("utf-8"), "package_configuration.md")

        self.assertNotIn("122.gif", extracted.text)
        self.assertNotIn("printer_icon.jpg", extracted.text)
        self.assertNotIn("[Welcome] > [PMS Operations]", extracted.text)
        self.assertNotIn("Previous | Next", extracted.text)

    def test_meaningful_markdown_content_is_chunked(self) -> None:
        extracted = extract_markdown(SAMPLE.encode("utf-8"), "package_configuration.md")
        chunks = chunk_by_headings_and_paragraphs(extracted.blocks)
        text = "\n\n".join(chunk.content for chunk in chunks)

        self.assertIn("Use Package Configuration", text)
        self.assertIn("Select Configuration", text)
        self.assertIn("Package Code | Unique package code.", text)
        self.assertTrue(all(chunk.heading for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
