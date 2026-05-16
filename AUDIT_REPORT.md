# Iri Collections Ecommerce Audit

Scope: repository scan plus a comparison against common ecommerce website features such as catalog browsing, search, cart, checkout, accounts, payments, order history, shipping, and trust/retention tools.

## What the site already does well

The codebase has a solid ecommerce core: product catalog browsing with category/search/sort, cart management, order creation, order cancellation, wishlist support, address management, guest checkout, OTP-based authentication, manual UPI payment flow, and admin analytics. The implementation also shows unusually strong backend discipline in places, including transaction locking, stock reservation handling, audit logging, and CSRF-aware cookie auth.

## Review findings

### 1. Guest checkout email update is not atomic

File: [store/views.py](store/views.py#L506)

The guest checkout OTP request path updates `request.user.email`, `full_name`, and `phone` after a duplicate-email check, but the check and save are not wrapped in an atomic lock on the user row. Two guest sessions can race through the uniqueness check before either save lands, which creates a TOCTOU window around the guest-to-email transition.

Why it matters: guest checkout is tied to a real email address for OTP and future account conversion, so this is a user-facing data integrity risk.

### 2. Product catalog listing is unpaginated

File: [store/views.py](store/views.py#L173)

The public product list explicitly disables pagination. That is fine for a small catalog, but it turns into a scalability problem as inventory grows because the API returns all matching products in one response.

Why it matters: ecommerce catalogs tend to grow faster than most other site surfaces, and unbounded list responses make search, filtering, and initial page loads progressively heavier.

### 3. Order address length is enforced only in the serializer

File: [store/models.py](store/models.py#L186)

`Order.shipping_address` is a plain `TextField`, while the serializer caps input at 500 characters. The database schema itself does not enforce the practical limit, so the constraint only exists as request-layer validation.

Why it matters: model-level bounds are more reliable than API-only bounds, especially if other code paths write orders later.


## Feature coverage vs common ecommerce expectations

Implemented well:

- Catalog browsing with search, category filtering, featured sorting, and featured badges.
- Cart, wishlist, checkout, order history, invoice page, and shipping fee calculation.
- Guest checkout and account conversion.
- Email OTP flows for signup, reset, and guest checkout.
- Manual UPI payment flow with QR generation.
- Admin dashboard, analytics, and order/product management.

Not found in the scan:

- Discount codes or coupons.
- Product reviews and ratings.
- Saved wishlists sharing, recommendation engine, or abandoned-cart recovery.
- Customer-facing order search/filtering beyond the order list.

## Overall assessment

The platform is beyond a basic storefront. It has a complete order funnel and a fairly mature security posture for a Django app. The biggest review items are operational rather than structural: guest identity transitions, catalog scalability, and the manual-payment trust model.

## Priority recommendations

1. Harden guest email updates with atomic locking.
2. Add pagination to public catalog endpoints before the catalog grows.
3. Move hard constraints like address length into the database model where possible.