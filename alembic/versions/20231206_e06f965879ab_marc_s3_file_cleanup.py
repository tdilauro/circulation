"""MARC S3 file cleanup.

Revision ID: e06f965879ab
Revises: 0039f3f12014
Create Date: 2023-12-06 16:04:36.936466+00:00

"""
from typing import Optional
from urllib.parse import urlparse

from alembic import op
from core.migration.util import migration_logger
from core.service.container import container_instance

# revision identifiers, used by Alembic.
revision = "e06f965879ab"
down_revision = "0039f3f12014"
branch_labels = None
depends_on = None


def parse_key_from_url(url: str, bucket: str) -> Optional[str]:
    """Parse the key from a URL.

    :param url: The URL to parse.
    :return: The key, or None if the URL is not a valid S3 URL.
    """
    parsed_url = urlparse(url)

    if f"/{bucket}/" in parsed_url.path:
        return parsed_url.path.split(f"/{bucket}/", 1)[1]

    if bucket in parsed_url.netloc:
        return parsed_url.path.lstrip("/")

    return None


def upgrade() -> None:
    # Before removing the cachedmarcfiles table, we want to clean up
    # the cachedmarcfiles stored in s3.
    #
    # Note: if you are running this migration on a development system and you want
    # to skip deleting these files you can just comment out the migration code below.
    services = container_instance()
    public_s3 = services.storage.public()
    log = migration_logger(revision)

    # Check if there are any cachedmarcfiles in s3
    connection = op.get_bind()
    cached_files = connection.execute(
        "SELECT r.mirror_url FROM cachedmarcfiles cmf JOIN representations r ON cmf.representation_id = r.id"
    ).all()
    if public_s3 is None and len(cached_files) > 0:
        raise RuntimeError(
            "There are cachedmarcfiles in the database, but no public s3 storage configured!"
        )

    keys_to_delete = []
    for cached_file in cached_files:
        url = cached_file.mirror_url
        bucket = public_s3.bucket
        key = parse_key_from_url(url, bucket)
        if key is None:
            raise RuntimeError(f"Unexpected URL format: {url} (bucket: {bucket})")
        generated_url = public_s3.generate_url(key)
        if generated_url != url:
            raise RuntimeError(f"URL mismatch: {url} != {generated_url}")
        keys_to_delete.append(key)

    for key in keys_to_delete:
        log.info(f"Deleting {key} from s3 bucket {public_s3.bucket}")
        public_s3.delete(key)


def downgrade() -> None:
    pass
