"""
grant_plan.py
=============

Admin utility for managing user plans.

Run this file from your project root, where database.py is located.

AVAILABLE OPERATIONS
--------------------

1. Permanent Pro
   python grant_plan.py user@example.com pro

2. Pro for days
   python grant_plan.py user@example.com pro --days 7

3. Pro for months
   python grant_plan.py user@example.com pro --months 3

4. Pro for months + days
   python grant_plan.py user@example.com pro --months 3 --days 15

5. Free plan
   python grant_plan.py user@example.com free

6. Revoke Pro
   python grant_plan.py user@example.com revoke

7. Check user status
   python grant_plan.py user@example.com status

IMPORTANT
---------

- "revoke" changes the user's plan to FREE.
- "free" changes the plan to FREE and removes the expiry date.
- "pro" without --months or --days gives permanent Pro.
- "pro --days N" gives Pro for N days.
- "pro --months N" gives Pro for N months.
- "pro --months N --days N" gives Pro for both periods.
"""

import argparse
import sys

from database import get_db_connection


# ============================================================
# CONSTANTS
# ============================================================

VALID_ACTIONS = (
    "pro",
    "free",
    "revoke",
    "status",
)


# ============================================================
# FIND USER
# ============================================================

def find_user(cursor, email):
    """
    Find a user using their email address.
    """

    cursor.execute(
        """
        SELECT
            id,
            name,
            email,
            plan,
            plan_expires_at
        FROM Users
        WHERE email = %s
        """,
        (email,)
    )

    return cursor.fetchone()


# ============================================================
# PRINT USER STATUS
# ============================================================

def print_status(user):
    """
    Display user information.
    """

    print()
    print("=" * 60)
    print("                    USER STATUS")
    print("=" * 60)

    print(f"ID              : {user['id']}")
    print(f"Name            : {user['name']}")
    print(f"Email           : {user['email']}")
    print(f"Plan            : {user['plan']}")

    if user["plan_expires_at"]:
        print(f"Plan expires    : {user['plan_expires_at']}")
    else:
        print("Plan expires    : Never")

    print("=" * 60)
    print()


# ============================================================
# GRANT PRO PLAN
# ============================================================

def grant_pro(
    cursor,
    user_id,
    months=None,
    days=None
):
    """
    Grant Pro.

    Possible combinations:

    No values:
        Permanent Pro

    days:
        Pro for N days

    months:
        Pro for N months

    months + days:
        Pro for N months + N days
    """

    # --------------------------------------------------------
    # PERMANENT PRO
    # --------------------------------------------------------

    if months is None and days is None:

        cursor.execute(
            """
            UPDATE Users
            SET
                plan = 'pro',
                plan_expires_at = NULL
            WHERE id = %s
            """,
            (user_id,)
        )

        return "Permanent"


    # --------------------------------------------------------
    # MONTHS + DAYS
    # --------------------------------------------------------

    if months is not None and days is not None:

        if months <= 0:
            raise ValueError(
                "Months must be greater than 0."
            )

        if days <= 0:
            raise ValueError(
                "Days must be greater than 0."
            )

        cursor.execute(
            """
            UPDATE Users
            SET
                plan = 'pro',
                plan_expires_at =
                    DATE_ADD(
                        DATE_ADD(
                            NOW(),
                            INTERVAL %s MONTH
                        ),
                        INTERVAL %s DAY
                    )
            WHERE id = %s
            """,
            (
                months,
                days,
                user_id
            )
        )

        return f"{months} month(s) + {days} day(s)"


    # --------------------------------------------------------
    # MONTHS ONLY
    # --------------------------------------------------------

    if months is not None:

        if months <= 0:
            raise ValueError(
                "Months must be greater than 0."
            )

        cursor.execute(
            """
            UPDATE Users
            SET
                plan = 'pro',
                plan_expires_at =
                    DATE_ADD(
                        NOW(),
                        INTERVAL %s MONTH
                    )
            WHERE id = %s
            """,
            (
                months,
                user_id
            )
        )

        return f"{months} month(s)"


    # --------------------------------------------------------
    # DAYS ONLY
    # --------------------------------------------------------

    if days is not None:

        if days <= 0:
            raise ValueError(
                "Days must be greater than 0."
            )

        cursor.execute(
            """
            UPDATE Users
            SET
                plan = 'pro',
                plan_expires_at =
                    DATE_ADD(
                        NOW(),
                        INTERVAL %s DAY
                    )
            WHERE id = %s
            """,
            (
                days,
                user_id
            )
        )

        return f"{days} day(s)"


# ============================================================
# SET FREE
# ============================================================

def set_free(cursor, user_id):
    """
    Change the user to Free.

    Expiry is removed.
    """

    cursor.execute(
        """
        UPDATE Users
        SET
            plan = 'free',
            plan_expires_at = NULL
        WHERE id = %s
        """,
        (user_id,)
    )


# ============================================================
# REVOKE PRO
# ============================================================

def revoke_plan(cursor, user_id):
    """
    Revoke paid plan access.

    User becomes Free.

    This does NOT delete:
        - user
        - payments
        - documents
        - document PINs
        - other records
    """

    cursor.execute(
        """
        UPDATE Users
        SET
            plan = 'free',
            plan_expires_at = NULL
        WHERE id = %s
        """,
        (user_id,)
    )


# ============================================================
# MAIN USER OPERATION
# ============================================================

def update_user(
    email,
    action,
    months=None,
    days=None
):
    """
    Execute the requested operation.
    """

    db = None
    cursor = None

    try:

        # ----------------------------------------------------
        # DATABASE CONNECTION
        # ----------------------------------------------------

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )


        # ----------------------------------------------------
        # FIND USER
        # ----------------------------------------------------

        user = find_user(
            cursor,
            email
        )

        if not user:

            print()
            print(
                f"No user found with email: {email}"
            )
            print()

            return False


        # ====================================================
        # STATUS
        # ====================================================

        if action == "status":

            print_status(user)

            return True


        # ====================================================
        # GRANT PRO
        # ====================================================

        if action == "pro":

            old_plan = user["plan"]

            duration = grant_pro(
                cursor,
                user["id"],
                months=months,
                days=days
            )

            db.commit()

            updated_user = find_user(
                cursor,
                email
            )

            print()
            print("=" * 60)
            print("                  PRO GRANTED")
            print("=" * 60)

            print(
                f"Name            : "
                f"{updated_user['name']}"
            )

            print(
                f"Email           : "
                f"{updated_user['email']}"
            )

            print(
                f"Previous plan   : "
                f"{old_plan}"
            )

            print(
                f"Current plan    : "
                f"{updated_user['plan']}"
            )

            print(
                f"Duration        : "
                f"{duration}"
            )

            if updated_user["plan_expires_at"]:
                print(
                    f"Expires         : "
                    f"{updated_user['plan_expires_at']}"
                )
            else:
                print("Expires         : Never")

            print("=" * 60)
            print()

            return True


        # ====================================================
        # FREE
        # ====================================================

        if action == "free":

            old_plan = user["plan"]

            set_free(
                cursor,
                user["id"]
            )

            db.commit()

            print()
            print("=" * 60)
            print("                  FREE PLAN")
            print("=" * 60)

            print(
                f"Name            : "
                f"{user['name']}"
            )

            print(
                f"Email           : "
                f"{user['email']}"
            )

            print(
                f"Previous plan   : "
                f"{old_plan}"
            )

            print("Current plan    : free")
            print("Expiry          : Never")

            print("=" * 60)
            print()

            return True


        # ====================================================
        # REVOKE
        # ====================================================

        if action == "revoke":

            old_plan = user["plan"]

            revoke_plan(
                cursor,
                user["id"]
            )

            db.commit()

            print()
            print("=" * 60)
            print("                 PLAN REVOKED")
            print("=" * 60)

            print(
                f"Name            : "
                f"{user['name']}"
            )

            print(
                f"Email           : "
                f"{user['email']}"
            )

            print(
                f"Previous plan   : "
                f"{old_plan}"
            )

            print("Current plan    : free")
            print("Expiry          : Never")

            print("=" * 60)
            print()

            return True


        # ====================================================
        # INVALID ACTION
        # ====================================================

        print(
            f"Unknown action: {action}"
        )

        return False


    except Exception as e:

        if db:
            db.rollback()

        print()
        print("=" * 60)
        print("                  ERROR")
        print("=" * 60)

        print(e)

        print("=" * 60)
        print()

        return False


    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()


# ============================================================
# COMMAND LINE INTERFACE
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Manage user plans."
        )
    )


    # --------------------------------------------------------
    # EMAIL
    # --------------------------------------------------------

    parser.add_argument(
        "email",
        help="User's email address"
    )


    # --------------------------------------------------------
    # ACTION
    # --------------------------------------------------------

    parser.add_argument(
        "action",
        choices=VALID_ACTIONS,
        help=(
            "pro, free, revoke or status"
        )
    )


    # --------------------------------------------------------
    # MONTHS
    # --------------------------------------------------------

    parser.add_argument(
        "--months",
        type=int,
        default=None,
        help=(
            "Number of months for Pro. "
            "Any positive number."
        )
    )


    # --------------------------------------------------------
    # DAYS
    # --------------------------------------------------------

    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help=(
            "Number of days for Pro. "
            "Any positive number."
        )
    )


    # ========================================================
    # VALIDATION
    # ========================================================

    # --months and --days only work with Pro.

    if (
        args := parser.parse_args()
    ):

        if (
            args.action != "pro"
            and (
                args.months is not None
                or args.days is not None
            )
        ):

            parser.error(
                "--months and --days can only "
                "be used with 'pro'."
            )


        # ----------------------------------------------------
        # Validate months
        # ----------------------------------------------------

        if (
            args.months is not None
            and args.months <= 0
        ):

            parser.error(
                "--months must be greater than 0."
            )


        # ----------------------------------------------------
        # Validate days
        # ----------------------------------------------------

        if (
            args.days is not None
            and args.days <= 0
        ):

            parser.error(
                "--days must be greater than 0."
            )


        # ====================================================
        # EXECUTE
        # ====================================================

        success = update_user(
            email=args.email,
            action=args.action,
            months=args.months,
            days=args.days
        )


        sys.exit(
            0 if success else 1
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()