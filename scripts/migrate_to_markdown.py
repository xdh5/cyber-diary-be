"""
Migration script to convert legacy diary content to rich-text HTML.
The original content will be backed up in content_legacy if changed.
"""

from sqlmodel import Session, select

from app.core.content import extract_first_image_url, normalize_entry_content
from app.core.timezone import now_shanghai
from app.db.session import engine
from app.models.models import Entry
from app.crud.crud import update_entry


def convert_entry_to_html(entry: Entry) -> tuple[str, bool]:
    normalized = normalize_entry_content(entry.content or "")
    changed = normalized != (entry.content or "")
    return normalized, changed


def migrate_entries():
    """Migrate all entries to rich-text HTML format"""
    with Session(engine) as session:
        entries = session.exec(select(Entry)).all()

        migrated_count = 0
        format_only_count = 0
        for entry in entries:
            try:
                new_content, changed = convert_entry_to_html(entry)
                current_format = (entry.content_format or "").strip().lower()
                if changed and not entry.content_legacy:
                    entry.content_legacy = entry.content
                if changed:
                    entry.content = new_content

                if (not entry.photo_url) and new_content:
                    first_image = extract_first_image_url(new_content)
                    if first_image:
                        entry.photo_url = first_image

                if changed or current_format != "html":
                    entry.content_format = "html"
                    entry.updated_at = now_shanghai()
                    update_entry(session, entry)
                    if changed:
                        migrated_count += 1
                    else:
                        format_only_count += 1
                    print(f"✅ Migrated entry {entry.id}: {entry.title}")
            except Exception as e:
                print(f"❌ Failed to migrate entry {entry.id}: {e}")

        print(
            f"\n✅ Migration complete! {migrated_count} entries content-converted, "
            f"{format_only_count} entries format-updated"
        )


if __name__ == "__main__":
    migrate_entries()
