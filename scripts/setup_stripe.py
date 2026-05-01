"""
Setup Stripe products and prices for OddsIntel.

Creates:
- Pro product: monthly (€4.99), yearly (€39.99), founding monthly (€3.99)
- Elite product: monthly (€14.99), yearly (€119.99), founding monthly (€9.99)
- Founding member coupon (100% off discount applied to bring price to founder rate)

Run: source venv/bin/activate && python scripts/setup_stripe.py
"""

import os
import stripe
from dotenv import load_dotenv

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def create_product_with_prices(name, description, monthly_price, yearly_price, founding_price):
    print(f"\n--- Creating {name} ---")

    product = stripe.Product.create(
        name=name,
        description=description,
    )
    print(f"Product: {product.id}")

    monthly = stripe.Price.create(
        product=product.id,
        unit_amount=int(monthly_price * 100),  # cents
        currency="eur",
        recurring={"interval": "month"},
        nickname=f"{name} Monthly",
    )
    print(f"Monthly price: {monthly.id}  (€{monthly_price}/mo)")

    yearly = stripe.Price.create(
        product=product.id,
        unit_amount=int(yearly_price * 100),
        currency="eur",
        recurring={"interval": "year"},
        nickname=f"{name} Yearly",
    )
    print(f"Yearly price:  {yearly.id}  (€{yearly_price}/yr)")

    founding = stripe.Price.create(
        product=product.id,
        unit_amount=int(founding_price * 100),
        currency="eur",
        recurring={"interval": "month"},
        nickname=f"{name} Founding Monthly",
    )
    print(f"Founding price: {founding.id}  (€{founding_price}/mo)")

    return {
        "product_id": product.id,
        "monthly_price_id": monthly.id,
        "yearly_price_id": yearly.id,
        "founding_price_id": founding.id,
    }


def main():
    print("Setting up Stripe products for OddsIntel...")

    pro = create_product_with_prices(
        name="OddsIntel Pro",
        description="Deep match intelligence — predictions, signals, and value bet analysis.",
        monthly_price=4.99,
        yearly_price=39.99,
        founding_price=3.99,
    )

    elite = create_product_with_prices(
        name="OddsIntel Elite",
        description="AI picks, CLV tracking, full signal suite, and verified track record.",
        monthly_price=14.99,
        yearly_price=119.99,
        founding_price=9.99,
    )

    print("\n\n=== ADD THESE TO YOUR .env AND VERCEL ENV VARS ===\n")
    print(f"STRIPE_PRO_PRODUCT_ID={pro['product_id']}")
    print(f"STRIPE_PRO_MONTHLY_PRICE_ID={pro['monthly_price_id']}")
    print(f"STRIPE_PRO_YEARLY_PRICE_ID={pro['yearly_price_id']}")
    print(f"STRIPE_PRO_FOUNDING_PRICE_ID={pro['founding_price_id']}")
    print()
    print(f"STRIPE_ELITE_PRODUCT_ID={elite['product_id']}")
    print(f"STRIPE_ELITE_MONTHLY_PRICE_ID={elite['monthly_price_id']}")
    print(f"STRIPE_ELITE_YEARLY_PRICE_ID={elite['yearly_price_id']}")
    print(f"STRIPE_ELITE_FOUNDING_PRICE_ID={elite['founding_price_id']}")
    print("\n==================================================")


if __name__ == "__main__":
    main()
