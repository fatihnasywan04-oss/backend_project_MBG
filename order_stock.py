from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Event, Order, TicketClass


def restore_order_stock(db: Session, order: Order) -> None:
    """
    Mengembalikan stok tiket berdasarkan pesanan.

    Jika pesanan menggunakan kelas tiket, stok dikembalikan ke
    TicketClass. Jika tidak, stok dikembalikan ke Event.

    Fungsi ini tidak melakukan commit. Transaksi dikelola oleh
    fungsi pemanggil.
    """

    quantity = int(order.quantity)

    if quantity <= 0:
        raise ValueError("Jumlah tiket pada pesanan tidak valid.")

    ticket_class_id = getattr(order, "ticket_class_id", None)

    if ticket_class_id is not None:
        ticket_class = db.scalar(
            select(TicketClass)
            .where(
                TicketClass.id == ticket_class_id,
                TicketClass.event_id == order.event_id,
            )
            .with_for_update()
        )

        if ticket_class is None:
            raise ValueError(
                "Kelas tiket untuk pesanan ini tidak ditemukan."
            )

        ticket_class.available_tickets += quantity

    else:
        event = db.scalar(
            select(Event)
            .where(Event.id == order.event_id)
            .with_for_update()
        )

        if event is None:
            raise ValueError(
                "Event untuk pesanan ini tidak ditemukan."
            )

        event.available_tickets += quantity