
import getpass

from pydantic import EmailStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import SessionLocal
from models import User
from auth import hash_password


def main():
    print("\n=== PEMBUATAN AKUN SUPER ADMIN ===")

    name = input("Nama admin: ").strip()
    email = input("Email admin: ").strip().lower()

    password = getpass.getpass(
        "Password admin (minimal 8 karakter): "
    )

    confirm_password = getpass.getpass(
        "Konfirmasi password: "
    )

    # -----------------------------------------------------
    # VALIDASI NAMA
    # -----------------------------------------------------

    if len(name) < 2:
        print("Gagal: nama minimal 2 karakter.")
        return

    if len(name) > 100:
        print("Gagal: nama maksimal 100 karakter.")
        return

    # -----------------------------------------------------
    # VALIDASI EMAIL
    # -----------------------------------------------------

    try:
        email = str(
            EmailStr._validate(email)
        ).strip().lower()

    except (ValidationError, ValueError, TypeError):
        print("Gagal: format email tidak valid.")
        return

    # -----------------------------------------------------
    # VALIDASI PASSWORD
    # -----------------------------------------------------

    if len(password) < 8:
        print("Gagal: password minimal 8 karakter.")
        return

    if len(password) > 128:
        print("Gagal: password maksimal 128 karakter.")
        return

    if password != confirm_password:
        print("Gagal: konfirmasi password tidak cocok.")
        return

    db = SessionLocal()

    try:
        # -------------------------------------------------
        # CEK EMAIL DUPLIKAT
        # -------------------------------------------------

        existing_user = db.scalar(
            select(User).where(
                User.email == email
            )
        )

        if existing_user:
            print("\nEmail sudah terdaftar.")

            if existing_user.role == "admin":
                print(
                    "Akun tersebut sudah memiliki "
                    "akses Super Admin."
                )

            elif existing_user.role == "admin_event":
                print(
                    "Email tersebut digunakan akun Admin Event."
                )

            else:
                print(
                    "Email tersebut digunakan akun user. "
                    "Gunakan email lain."
                )

            return

        # -------------------------------------------------
        # BUAT SUPER ADMIN
        # -------------------------------------------------

        admin = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            role="admin",
            admin_event_status="none",
            admin_event_reviewed_by=None,
            admin_event_reviewed_at=None,
            admin_event_rejection_reason=None,
        )

        db.add(admin)
        db.commit()
        db.refresh(admin)

        print("\nAkun Super Admin berhasil dibuat!")
        print(f"Nama  : {admin.name}")
        print(f"Email : {admin.email}")
        print(f"Role  : {admin.role}")

    except IntegrityError:
        db.rollback()

        print(
            "\nGagal: email sudah digunakan "
            "oleh akun lain."
        )

    except SQLAlchemyError:
        db.rollback()

        print(
            "\nGagal menyimpan akun Super Admin. "
            "Periksa koneksi database dan terminal."
        )

    except Exception:
        db.rollback()

        print(
            "\nTerjadi kesalahan saat membuat akun Super Admin."
        )

        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()
