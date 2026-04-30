/* Core storefront runtime with cookie-authenticated API requests. */

function escapeHTML(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function safeJsonParse(value, fallback = null) {
    try {
        return value ? JSON.parse(value) : fallback;
    } catch {
        return fallback;
    }
}

function createRequestController() {
    const controller = new AbortController();
    RouteRuntime.controllers.add(controller);
    controller.signal.addEventListener('abort', () => RouteRuntime.controllers.delete(controller), { once: true });
    return controller;
}

const RouteRuntime = {
    controllers: new Set(),

    abortAll() {
        for (const controller of Array.from(this.controllers)) {
            controller.abort();
        }
        this.controllers.clear();
    },

    renderPageError(target, message, retryLabel = 'Retry') {
        const container = typeof target === 'string' ? document.querySelector(target) : target;
        if (!container) return;
        container.innerHTML = `
            <div class="brutalist-card" style="padding: 60px; text-align: center; background: var(--error-bg); color: var(--error);">
                <div style="margin-bottom: var(--space-md);">
                    <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>
                </div>
                <h3 style="font-size: 1.5rem; margin-bottom: 8px;">SYSTEM ERROR</h3>
                <p style="font-weight: 500; margin-bottom: var(--space-md); opacity: 0.8;">${escapeHTML(message)}</p>
                <button class="brutalist-btn" style="background: var(--error); color: var(--text-inverted); border-color: var(--error);" type="button" onclick="window.location.reload()">${escapeHTML(retryLabel)}</button>
            </div>
        `;
    },

    installGlobalErrorGuards() {
        window.addEventListener('error', (event) => {
            if (event?.error?.name === 'AbortError') return;
            Toast.error('A page error occurred. Please retry.');
        });

        window.addEventListener('unhandledrejection', (event) => {
            if (event?.reason?.name === 'AbortError') return;
            Toast.error('A network error occurred. Please retry.');
        });

        window.addEventListener('beforeunload', () => this.abortAll());
    },
};

const API = {
    base: '/api',
    userCacheKey: 'iri_user',
    bootstrapPromise: null,
    sessionValidated: false,

    getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    },

    getUser() {
        return safeJsonParse(sessionStorage.getItem(this.userCacheKey), null);
    },

    setUser(user) {
        if (!user) {
            sessionStorage.removeItem(this.userCacheKey);
            this.isBootstrapped = false;
            this.sessionValidated = false;
            return;
        }
        sessionStorage.setItem(this.userCacheKey, JSON.stringify(user));
        this.isBootstrapped = true;
        this.sessionValidated = true;
    },

    clearSession(options = {}) {
        const preserveGuestCart = !!options.preserveGuestCart;
        sessionStorage.clear();
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('user');
        if (!preserveGuestCart) {
            localStorage.removeItem('iri_guest_cart');
        }
        this.isBootstrapped = false;
        this.sessionValidated = false;
    },

    isLoggedIn() {
        return !!this.getUser();
    },

    isAdmin() {
        const user = this.getUser();
        return !!user && user.role === 'admin';
    },

    async parseResponse(res) {
        const contentType = res.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            return await res.json();
        }
        const text = await res.text();
        return text ? { detail: text } : null;
    },

    async refreshToken() {
        const csrfToken = this.getCsrfToken();
        const headers = {};
        if (csrfToken) headers['X-CSRFToken'] = csrfToken;

        try {
            const res = await fetch(`${this.base}/auth/refresh/`, {
                method: 'POST',
                credentials: 'same-origin',
                headers,
            });
            return res.ok;
        } catch {
            return false;
        }
    },

    async request(endpoint, options = {}) {
        const url = `${this.base}${endpoint}`;
        const method = (options.method || 'GET').toUpperCase();
        const headers = { ...options.headers };
        const isFormData = options.body instanceof FormData;
        const unsafeMethod = !['GET', 'HEAD', 'OPTIONS'].includes(method);

        if (!isFormData && options.body !== undefined && !headers['Content-Type']) {
            headers['Content-Type'] = 'application/json';
        }
        if (unsafeMethod) {
            const csrfToken = this.getCsrfToken();
            if (csrfToken) headers['X-CSRFToken'] = csrfToken;
        }

        const requestOptions = {
            ...options,
            method,
            headers,
            credentials: 'same-origin',
        };

        let res = await fetch(url, requestOptions);

        if (res.status === 401 && !options.skipRefresh) {
            const refreshed = await this.refreshToken();
            if (refreshed) {
                res = await fetch(url, { ...requestOptions, headers });
            } else {
                this.clearSession({ preserveGuestCart: true });
            }
        }

        return res;
    },

    async get(endpoint, options = {}) {
        const res = await this.request(endpoint, { ...options, method: 'GET' });
        if (!res.ok) throw Object.assign(new Error('Request failed'), { response: res, data: await this.parseResponse(res) });
        return this.parseResponse(res);
    },

    async post(endpoint, data, options = {}) {
        const res = await this.request(endpoint, {
            ...options,
            method: 'POST',
            body: data instanceof FormData ? data : JSON.stringify(data),
        });
        return { ok: res.ok, status: res.status, data: await this.parseResponse(res) };
    },

    async patch(endpoint, data, options = {}) {
        const res = await this.request(endpoint, {
            ...options,
            method: 'PATCH',
            body: JSON.stringify(data),
        });
        return { ok: res.ok, status: res.status, data: await this.parseResponse(res) };
    },

    async delete(endpoint, data = null, options = {}) {
        const requestOptions = { ...options, method: 'DELETE' };
        if (data) requestOptions.body = JSON.stringify(data);
        const res = await this.request(endpoint, requestOptions);
        return { ok: res.ok, status: res.status, data: await this.parseResponse(res) };
    },

    isBootstrapped: false,

    async bootstrapUser(options = {}) {
        const forceRefresh = !!options.forceRefresh;
        if (this.isBootstrapped && this.sessionValidated && !forceRefresh) return this.getUser();
        if (this.bootstrapPromise) return this.bootstrapPromise;

        this.bootstrapPromise = (async () => {
            try {
                const controller = createRequestController();
                const user = await this.get('/auth/profile/', {
                    signal: controller.signal,
                });
                this.setUser(user);
                this.isBootstrapped = true;
                this.sessionValidated = true;
                return user;
            } catch (error) {
                const status = error?.response?.status;
                if (status === 401 || status === 403) {
                    this.clearSession({ preserveGuestCart: true });
                    this.isBootstrapped = true;
                    this.sessionValidated = true;
                    return null;
                }

                const cachedUser = this.getUser();
                // Fall back to cache only for transient network/server failures.
                if (cachedUser) {
                    this.isBootstrapped = true;
                    this.sessionValidated = false;
                    return cachedUser;
                }

                this.isBootstrapped = true;
                this.sessionValidated = false;
                return null;
            } finally {
                this.bootstrapPromise = null;
            }
        })();

        return this.bootstrapPromise;
    },
};

const Toast = {
    container: null,

    init() {
        this.container = document.getElementById('toast-container');
        if (!this.container) {
            this.container = document.createElement('div');
            this.container.className = 'toast-container';
            this.container.id = 'toast-container';
            document.body.appendChild(this.container);
        }
    },

    show(message, type = 'info', duration = 3000) {
        if (!this.container) this.init();
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        this.container.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 400);
        }, duration);
    },

    success(message) { this.show(message, 'success'); },
    error(message) { this.show(message, 'error'); },
    info(message) { this.show(message, 'info'); },
};

function initLazyLoading() {
    document.querySelectorAll('img:not([loading])').forEach((img) => {
        img.setAttribute('loading', 'lazy');
        img.setAttribute('decoding', 'async');
    });

    const images = document.querySelectorAll('img[data-src]');
    if (!images.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const img = entry.target;
            const targetSrc = img.dataset.src;
            if (targetSrc && targetSrc !== 'undefined' && targetSrc !== 'null') {
                img.src = targetSrc;
            }
            img.removeAttribute('data-src');
            img.addEventListener('load', () => img.classList.add('loaded'), { once: true });
            observer.unobserve(img);
        });
    }, { rootMargin: '100px' });

    images.forEach((img) => observer.observe(img));
}

async function updateNavbar() {
    const authLinks = document.getElementById('auth-links');
    const userMenu = document.getElementById('user-menu');
    const cartCount = document.getElementById('cart-count');
    const adminLink = document.getElementById('admin-link');
    const wishlistLink = document.getElementById('wishlist-link');

    if (!authLinks) return;

    const user = await API.bootstrapUser();
    if (user) {
        authLinks.classList.add('hidden');
        authLinks.style.display = 'none';
        if (userMenu) {
            userMenu.classList.remove('hidden');
            userMenu.style.display = '';
            const nameEl = userMenu.querySelector('.user-name-trigger');
            if (nameEl) {
                const firstName = user.full_name ? user.full_name.split(' ')[0] : (user.username || 'Account');
                nameEl.textContent = firstName;
            }
        }
        if (adminLink && API.isAdmin()) {
            adminLink.classList.remove('hidden');
            adminLink.style.display = '';
        }
        if (wishlistLink) {
            wishlistLink.classList.remove('hidden');
            wishlistLink.style.display = '';
        }
        updateCartCount();
    } else {
        authLinks.classList.remove('hidden');
        authLinks.style.display = '';
        if (userMenu) {
            userMenu.classList.add('hidden');
            userMenu.style.display = 'none';
        }
        if (adminLink) {
            adminLink.classList.add('hidden');
            adminLink.style.display = 'none';
        }
        if (wishlistLink) {
            wishlistLink.classList.add('hidden');
            wishlistLink.style.display = 'none';
        }
        if (cartCount) cartCount.textContent = '0';
    }

    // Highlight active nav link (flat structure, no li wrappers)
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-links > a.nav-link').forEach((link) => {
        const href = link.getAttribute('href');
        if (href === currentPath || (href !== '/' && currentPath.startsWith(href))) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });
}

function updateWishlistBadge() {
    const badge = document.getElementById('wishlist-count');
    if (badge) {
        const count = window.wishlistItems ? window.wishlistItems.size : 0;
        badge.textContent = count;
        if (count === 0) badge.style.display = 'none';
        else badge.style.display = 'flex';
    }
}

async function updateCartCount() {
    try {
        const controller = createRequestController();
        const cart = await API.get('/store/cart/', { signal: controller.signal });
        const badge = document.getElementById('cart-count');
        if (badge && cart) {
            const count = parseInt(cart.item_count || 0);
            badge.textContent = count;
            if (count === 0) badge.style.display = 'none';
            else badge.style.display = 'flex';
        }
    } catch {
        const badge = document.getElementById('cart-count');
        if (badge) {
            badge.textContent = '0';
            badge.style.display = 'none';
        }
    }
}

async function logout() {
    try {
        RouteRuntime.abortAll();
        await API.post('/auth/logout/', {});
    } catch {
        // Clearing the local session is still safe even if the network fails.
    }
    API.clearSession();
    Toast.success('Logged out successfully');
    setTimeout(() => { window.location.href = '/'; }, 500);
}

function formatPrice(price) {
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    }).format(price);
}

function productCardHTML(product) {
    const discount = product.compare_price
        ? Math.round((1 - product.price / product.compare_price) * 100)
        : 0;
    let imgSrc = product.display_image || product.image_url || product.image || '';
    if (imgSrc === 'undefined' || imgSrc === 'null') imgSrc = '';
    
    const isOOS = product.stock <= 0;
    const cardOpacity = isOOS ? '0.6' : '1';
    const isInWishlist = window.wishlistItems ? window.wishlistItems.has(product.id) : false;
    const heartIcon = `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="${isInWishlist ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>`;

    return `
        <div class="brutalist-card product-card" onclick="window.location.href='/product/${product.slug}/'" data-product-id="${product.id}" style="opacity: ${cardOpacity};">
            <div class="product-image-container">
                <img data-src="${imgSrc}" alt="${escapeHTML(product.name)}" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 400'%3E%3Crect fill='%23F4F4F4' width='400' height='400'/%3E%3C/svg%3E">
                ${discount > 0 && !isOOS ? `<span class="badge" style="background:var(--bg-accent); position:absolute; top:10px; left:10px;">${discount}% OFF</span>` : ''}
                ${isOOS ? `<span class="badge" style="background:var(--text-primary); color:var(--text-inverted); position:absolute; top:10px; left:10px;">SOLD OUT</span>` : ''}
                <button class="brutalist-btn" onclick="event.stopPropagation(); toggleWishlist(${product.id})" id="btn-wishlist-${product.id}" style="position:absolute; top:10px; right:10px; width:36px; height:36px; box-shadow:2px 2px 0 0 var(--shadow-color); background:var(--bg-elevated); padding:0;">${heartIcon}</button>
            </div>
            <div class="product-info">
                <div style="font-size: 0.7rem; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.06em;">${escapeHTML(product.category_name || 'JEWELRY')}</div>
                <h3 style="font-size: 0.95rem; margin-bottom: 8px;">${escapeHTML(product.name)}</h3>
                <div style="display:flex; align-items:center; justify-content:space-between;">
                    <div class="product-price">${formatPrice(product.price)}</div>
                    ${isOOS 
                        ? `<span style="font-weight:800; font-size:0.7rem;">OUT</span>`
                        : `<button class="brutalist-btn" onclick="event.stopPropagation(); addToCart(${product.id})" style="width:36px; height:36px; box-shadow:2px 2px 0 0 var(--shadow-color); padding:0;">
                             <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
                           </button>`
                    }
                </div>
            </div>
        </div>
    `;
}

// ─── Guest Cart (localStorage for anonymous users) ──────────────────────
const GuestCart = {
    key: 'iri_guest_cart',

    get() {
        return safeJsonParse(localStorage.getItem(this.key), []);
    },

    save(items) {
        localStorage.setItem(this.key, JSON.stringify(items));
    },

    add(productId, quantity = 1) {
        const items = this.get();
        const existing = items.find(i => i.product_id === productId);
        if (existing) {
            existing.quantity += quantity;
        } else {
            items.push({ product_id: productId, quantity });
        }
        this.save(items);
    },

    clear() {
        localStorage.removeItem(this.key);
    },

    isEmpty() {
        return this.get().length === 0;
    },
};

/**
 * Merge guest cart into the authenticated user's server-side cart.
 * Duplicate products are handled gracefully by the CartView.post endpoint
 * which adds quantities to existing cart items.
 */
async function mergeGuestCart() {
    const items = GuestCart.get();
    if (!items.length) return;

    for (const item of items) {
        try {
            await API.post('/store/cart/', {
                product_id: item.product_id,
                quantity: item.quantity,
            });
        } catch (err) {
            // Silently skip items that fail (e.g., out of stock, inactive).
            // The user's authenticated cart will still contain whatever succeeded.
            console.warn('Failed to merge guest cart item:', item, err);
        }
    }

    GuestCart.clear();
    updateCartCount();
}

async function addToCart(productId, quantity = 1) {
    const user = await API.bootstrapUser();
    if (!user) {
        // Store in guest cart for merge after login
        GuestCart.add(productId, quantity);
        Toast.success('Added to cart — sign in to checkout');
        return;
    }

    const badge = document.getElementById('cart-count');
    const previousCount = Number.parseInt(badge?.textContent || '0', 10) || 0;
    if (badge) badge.textContent = String(previousCount + quantity);

    const response = await API.post('/store/cart/', { product_id: productId, quantity });
    if (response.ok) {
        Toast.success('Added to cart');
        updateCartCount();
    } else {
        if (badge) badge.textContent = String(previousCount);
        Toast.error(response.data?.error || 'Failed to add to cart');
    }
}

window.wishlistItems = new Set();

async function loadWishlistItems() {
    const user = await API.bootstrapUser();
    if (!user) return;

    try {
        const controller = createRequestController();
        const items = await API.get('/store/wishlist/', { signal: controller.signal });
        window.wishlistItems = new Set(items.map((item) => item.id));
        updateWishlistBadge();
    } catch {
        window.wishlistItems = new Set();
    }
}

async function toggleWishlist(productId) {
    const user = await API.bootstrapUser();
    if (!user) {
        Toast.info('Please login to save to wishlist');
        setTimeout(() => { window.location.href = '/login/'; }, 1000);
        return;
    }

    const wasPresent = window.wishlistItems.has(productId);
    updateWishlistUI(productId, !wasPresent);
    if (wasPresent) {
        window.wishlistItems.delete(productId);
    } else {
        window.wishlistItems.add(productId);
    }
    updateWishlistBadge();

    try {
        const response = wasPresent
            ? await API.delete('/store/wishlist/', { product_id: productId })
            : await API.post('/store/wishlist/', { product_id: productId });

        if (!response.ok) {
            throw new Error(response.data?.error || 'Wishlist update failed');
        }

        Toast.success(wasPresent ? 'Removed from wishlist' : 'Added to wishlist');
    } catch (error) {
        if (wasPresent) {
            window.wishlistItems.add(productId);
        } else {
            window.wishlistItems.delete(productId);
        }
        updateWishlistUI(productId, wasPresent);
        updateWishlistBadge();
        Toast.error(error.message || 'Failed to update wishlist');
    }
}

function updateWishlistUI(productId, added) {
    document.querySelectorAll(`#btn-wishlist-${productId}`).forEach((btn) => {
        btn.innerHTML = added 
            ? '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>' 
            : '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>';
        btn.style.transform = 'scale(1.2)';
        setTimeout(() => { btn.style.transform = 'none'; }, 200);
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    Toast.init();
    RouteRuntime.installGlobalErrorGuards();
    const isAuthPage = document.body.classList.contains('auth-body');
    if (!isAuthPage) {
        await updateNavbar();
        await loadWishlistItems();
    }
    initLazyLoading();

    const navbar = document.getElementById('navbar');
    const toggle = document.getElementById('nav-toggle');
    const links = document.getElementById('nav-links');
    const overlay = document.getElementById('menu-overlay');
    const userMenu = document.getElementById('user-menu');
    const userMenuToggle = document.getElementById('user-menu-toggle');
    const userMenuDropdown = document.getElementById('user-menu-dropdown');

    const handleScroll = () => {
        if (!navbar) return;
        if (window.scrollY > 20) {
            navbar.classList.add('scrolled');
        } else {
            navbar.classList.remove('scrolled');
        }
    };

    window.addEventListener('scroll', handleScroll);
    handleScroll();

    if (toggle && links && overlay) {
        const toggleMenu = () => {
            const isOpen = links.classList.toggle('open');
            toggle.classList.toggle('open');
            overlay.classList.toggle('active');
            document.body.style.overflow = isOpen ? 'hidden' : '';
        };

        toggle.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleMenu();
        });

        overlay.addEventListener('click', toggleMenu);

        if (userMenu && userMenuToggle && userMenuDropdown) {
            userMenu.style.position = 'relative';

            userMenuToggle.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                userMenuDropdown.classList.toggle('hidden');
            });

            document.addEventListener('click', (event) => {
                if (!userMenu.contains(event.target)) {
                    userMenuDropdown.classList.add('hidden');
                }
            });

            userMenuDropdown.querySelectorAll('a, button').forEach((item) => {
                item.addEventListener('click', () => {
                    userMenuDropdown.classList.add('hidden');
                });
            });
        }

        links.querySelectorAll('a').forEach((link) => {
            link.addEventListener('click', () => {
                if (links.classList.contains('open')) toggleMenu();
            });
        });
    }
});
