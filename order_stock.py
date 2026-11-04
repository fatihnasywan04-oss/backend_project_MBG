from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Order, TicketClass


def restore_order_stock(
    db: Session,
    order: Order,
) -> None:
    """
    Mengembalikan stok kelas tiket berdasarkan pesanan.

    Ketentuan:
    - Stok dikembalikan ke TicketClass.stock.
    - Event.available_tickets tidak diubah.
    - Stok hanya dikembalikan jika stock_reserved=True.
    - Mencegah pengembalian stok berulang.
    - Tidak melakukan commit.
      Transaksi dikelola oleh fungsi pemanggil.
    """

    # Pastikan stok belum pernah dikembalikan.
    if not order.stock_reserved:
        return

    quantity = int(order.quantity)

    if quantity <= 0:
        raise ValueError(
            "Jumlah tiket pada pesanan tidak valid."
        )

    if order.ticket_class_id is None:
        raise ValueError(
            "Pesanan tidak memiliki kelas tiket."
        )

    # Kunci baris kelas tiket untuk mencegah
    # konflik perubahan stok bersamaan.
    ticket_class = db.scalar(
        select(TicketClass)
        .where(
            TicketClass.id == order.ticket_class_id,
            TicketClass.event_id == order.event_id,
        )
        .with_for_update()
    )

    if ticket_class is None:
        raise ValueError(
            "Kelas tiket untuk pesanan ini tidak ditemukan."
        )

    # Kembalikan stok kelas tiket.
    ticket_class.stock += quantity

    # Tandai bahwa stok sudah dikembalikan.
    order.stock_reserved = False

    # Commit dilakukan oleh fungsi pemanggil.