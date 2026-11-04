import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError

from database import SessionLocal
from models import Event


logger = logging.getLogger(__name__)

# Interval pemeriksaan event dalam detik.
CLEANUP_INTERVAL = 60


async def cleanup_expired_events():
    """
    Melakukan soft delete pada event yang sudah selesai.

    Ketentuan:
    - Event yang sudah selesai disembunyikan dari publik.
    - Data event tidak dihapus permanen.
    - TicketClass tetap tersimpan.
    - Order dan Payment tetap tersimpan.
    - Tidak menghapus riwayat transaksi.
    - deleted_by bernilai None karena proses dilakukan
      otomatis oleh sistem.
    """

    while True:
        db = SessionLocal()

        try:
            now = datetime.now(timezone.utc)

            # Gunakan end_date jika tersedia.
            # Jika end_date kosong, gunakan event_date.
            expired_events = db.scalars(
                select(Event)
                .where(
                    func.coalesce(
                        Event.end_date,
                        Event.event_date,
                    ) < func.now(),
                    Event.is_deleted.is_(False),
                )
                .with_for_update(
                    skip_locked=True
                )
            ).all()

            deleted_count = 0

            for event in expired_events:
                event.is_deleted = True
                event.deleted_at = now
                event.deleted_by = None

                deleted_count += 1

            if deleted_count > 0:
                db.commit()

                logger.info(
                    "Berhasil melakukan soft delete pada %s event yang sudah selesai.",
                    deleted_count,
                )

        except SQLAlchemyError:
            db.rollback()

            logger.exception(
                "Database error saat menjalankan event cleanup."
            )

        except Exception:
            db.rollback()

            logger.exception(
                "Kesalahan saat menjalankan event cleanup."
            )

        finally:
            db.close()

        await asyncio.sleep(CLEANUP_INTERVAL)