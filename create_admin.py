import getpass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from database import SessionLocal
from models import User
from auth import hash_password


def main():
    print("\n=== PEMBUATAN AKUN ADMIN ===")

    name = input("Nama admin: ").strip()
    email = input("Email admin: ").strip().lower()
    password = getpass.getpass(
        "Password admin (minimal 8 karakter): "
    )

    if len(name) < 2:
        print("Gagal: nama minimal 2 karakter.")
        return

    if len(password) < 8:
        print("Gagal: password minimal 8 karakter.")
        return

    db = SessionLocal()

    try:
        existing_user = db.scalar(
            select(User).where(User.email == email)
        )

        if existing_user:
            print("\nEmail sudah terdaftar.")

            if existing_user.role == "admin":
                print("Akun tersebut sudah memiliki akses admin.")
            else:
                print(
                    "Email tersebut digunakan akun user. "
                    "Gunakan email lain untuk membuat admin."
                )

            return

        admin = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            role="admin",
        )

        db.add(admin)
        db.commit()
        db.refresh(admin)

        print("\nAkun admin berhasil dibuat!")
        print(f"Nama  : {admin.name}")
        print(f"Email : {admin.email}")
        print(f"Role  : {admin.role}")

    except SQLAlchemyError:
        db.rollback()
        print(
            "\nGagal menyimpan akun admin ke database. "
            "Periksa koneksi database dan terminal."
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()