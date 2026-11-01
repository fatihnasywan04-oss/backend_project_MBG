
import asyncio
import logging

from sqlalchemy import select, func

from database import SessionLocal
from models import Event
from routes.event_routes import permanently_delete_event


logger = logging.getLogger(__name__)

CLEANUP_INTERVAL = 60


async def cleanup_expired_events():
    """
    Menghapus event yang tanggal dan waktunya sudah lewat.
    Penghapusan dilakukan melalui helper agar data orders,
    payments, dan ticket_classes ikut terhapus.
    """

    while True:
        db = SessionLocal()

        try:
            expired_event_ids = (
                db.execute(
                    select(Event.id)
                    .where(Event.event_date < func.now())
                    .with_for_update(skip_locked=True)
                )
                .scalars()
                .all()
            )

            if expired_event_ids:
                for event_id in expired_event_ids:
                    permanently_delete_event(db, event_id)

                db.commit()

                logger.info(
                    "Berhasil menghapus %s event yang sudah lewat.",
                    len(expired_event_ids),
                )

        except Exception:
            db.rollback()
            logger.exception("Gagal menjalankan pembersihan event.")

        finally:
            db.close()

        await asyncio.sleep(CLEANUP_INTERVAL)
