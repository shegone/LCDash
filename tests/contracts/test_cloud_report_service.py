import unittest

from app.services.cloud_report_service import (
    ReportIntent, build_report_preview, create_report_template,
    safe_template_record, template_visible,
)


class Source:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def query(self, tenant_id, intent): self.calls.append((tenant_id, intent)); return self.rows
    def query_current(self, tenant_id, intent): self.calls.append((tenant_id, intent)); return self.rows


class CloudReportServiceTests(unittest.TestCase):
    def intent(self, **overrides):
        values = dict(metric="call_count", dimensions=("nature",), period="30d")
        values.update(overrides)
        return ReportIntent(**values)

    def test_rejects_non_allowlisted_query_shape(self):
        with self.assertRaisesRegex(ValueError, "metric"):
            self.intent(metric="raw_cad_payload")
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.intent(dimensions=("caller_name",))

    def test_database_is_always_queried_before_optional_current_cad(self):
        database, cad = Source(()), Source(({"nature": "Synthetic", "call_count": 2},))
        preview = build_report_preview(
            tenant_id="logan-synthetic", intent=self.intent(current_cad_fallback=True),
            analytics=database, current_cad=cad,
        )
        self.assertEqual(preview.source, "current-cad-read-only")
        self.assertEqual(len(database.calls), 1)
        self.assertEqual(len(cad.calls), 1)
        self.assertTrue(preview.save_requires_user_action)
        self.assertTrue(preview.export_requires_user_action)

    def test_database_result_prevents_cad_fallback(self):
        database, cad = Source(({"call_count": 3},)), Source(({"call_count": 4},))
        preview = build_report_preview(
            tenant_id="logan-synthetic", intent=self.intent(current_cad_fallback=True),
            analytics=database, current_cad=cad,
        )
        self.assertEqual(preview.source, "analytics-database")
        self.assertEqual(cad.calls, [])

    def test_template_is_tenant_and_role_scoped_without_results(self):
        template = create_report_template(
            tenant_id="logan-synthetic", title="Calls by nature",
            intent=self.intent(), author_subject="supervisor@example.test",
            visible_to_roles=("supervisor",),
        )
        record = safe_template_record(template)
        self.assertNotIn("rows", record)
        self.assertNotIn("conversation", record)
        self.assertTrue(template_visible(
            template, tenant_id="logan-synthetic", roles=frozenset({"supervisor"})
        ))
        self.assertFalse(template_visible(
            template, tenant_id="other-county", roles=frozenset({"supervisor"})
        ))
        self.assertFalse(template_visible(
            template, tenant_id="logan-synthetic", roles=frozenset({"viewer"})
        ))

    def test_visibility_respects_the_role_hierarchy(self):
        """Admin is supervisor-plus, so supervisor templates must not vanish
        for admins. Bit Ted on the first real save (2026-08-09): the save
        button marks templates visible to supervisor, he saved as admin, and
        the new list page showed him nothing."""
        supervisor_template = create_report_template(
            tenant_id="logan-synthetic", title="Calls by nature",
            intent=self.intent(), author_subject="tedsparks@911logan.com",
            visible_to_roles=("supervisor",),
        )
        for viewer_roles, expected in (
            ({"admin"}, True),          # the actual failure
            ({"supervisor"}, True),
            ({"user"}, False),          # hierarchy goes one way only
            ({"viewer"}, False),        # roles outside the hierarchy: exact-match
        ):
            with self.subTest(roles=viewer_roles):
                self.assertEqual(
                    template_visible(
                        supervisor_template,
                        tenant_id="logan-synthetic",
                        roles=frozenset(viewer_roles),
                    ),
                    expected,
                )

        user_template = create_report_template(
            tenant_id="logan-synthetic", title="Calls by hour",
            intent=self.intent(), author_subject="tedsparks@911logan.com",
            visible_to_roles=("user",),
        )
        self.assertTrue(template_visible(
            user_template, tenant_id="logan-synthetic", roles=frozenset({"admin"})
        ))
        self.assertTrue(template_visible(
            user_template, tenant_id="logan-synthetic", roles=frozenset({"supervisor"})
        ))
        # Hierarchy never crosses tenants.
        self.assertFalse(template_visible(
            supervisor_template, tenant_id="other-county", roles=frozenset({"admin"})
        ))

    def test_unsafe_template_visibility_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unauthorized"):
            create_report_template(
                tenant_id="logan-synthetic", title="Calls by nature",
                intent=self.intent(), author_subject="user@example.test",
                visible_to_roles=("external-delivery",),
            )


if __name__ == "__main__":
    unittest.main()
