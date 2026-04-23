from django.apps import AppConfig


class BasmaappConfig(AppConfig):
    name = 'basmaapp'

    def ready(self):
        # Django 5.0 on Python 3.14 can fail in admin submit_row due to Context(context)
        # coercion. Patch submit_row to return a plain dict context for all admin templates.
        try:
            from django.contrib.admin.templatetags import admin_modify
        except Exception:
            return

        def _safe_submit_row(context):
            add = context["add"]
            change = context["change"]
            is_popup = context["is_popup"]
            save_as = context["save_as"]
            show_save = context.get("show_save", True)
            show_save_and_add_another = context.get("show_save_and_add_another", True)
            show_save_and_continue = context.get("show_save_and_continue", True)
            has_add_permission = context["has_add_permission"]
            has_change_permission = context["has_change_permission"]
            has_view_permission = context["has_view_permission"]
            has_editable_inline_admin_formsets = context["has_editable_inline_admin_formsets"]
            can_save = (
                (has_change_permission and change)
                or (has_add_permission and add)
                or has_editable_inline_admin_formsets
            )
            can_save_and_add_another = (
                has_add_permission
                and not is_popup
                and (not save_as or add)
                and can_save
                and show_save_and_add_another
            )
            can_save_and_continue = (
                not is_popup and can_save and has_view_permission and show_save_and_continue
            )
            can_change = has_change_permission or has_editable_inline_admin_formsets

            if hasattr(context, "flatten"):
                ctx = context.flatten()
            else:
                ctx = dict(context)

            ctx.update(
                {
                    "can_change": can_change,
                    "show_delete_link": (
                        not is_popup
                        and context["has_delete_permission"]
                        and change
                        and context.get("show_delete", True)
                    ),
                    "show_save_as_new": not is_popup
                    and has_add_permission
                    and change
                    and save_as,
                    "show_save_and_add_another": can_save_and_add_another,
                    "show_save_and_continue": can_save_and_continue,
                    "show_save": show_save and can_save,
                    "show_close": not (show_save and can_save),
                }
            )
            return ctx

        admin_modify.submit_row = _safe_submit_row
