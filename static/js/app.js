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
            <div class="empty-state">
                <div class="empty-state-icon"><i class="ph ph-warning-circle"></i></div>
                <h3>Something went wrong</h3>
                <p>${escapeHTML(message)}</p>
                <button class="btn btn-primary mt-2" type="button" onclick="window.location.reload()">${escapeHTML(retryLabel)}</button>
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
            return;
        }
        sessionStorage.setItem(this.userCacheKey, JSON.stringify(user));
    },

    clearSession() {
        sessionStorage.removeItem(this.userCacheKey);
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('user');
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
                this.clearSession();
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

    async bootstrapUser() {
        if (this.bootstrapPromise) return this.bootstrapPromise;

        this.bootstrapPromise = (async () => {
            const cachedUser = this.getUser();
            if (cachedUser) return cachedUser;

            try {
                const controller = createRequestController();
                const user = await this.get('/auth/profile/', {
                    signal: controller.signal,
                    skipRefresh: true,
                });
                this.setUser(user);
                return user;
            } catch {
                this.clearSession();
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
    const images = document.querySelectorAll('img[data-src]');
    if (!images.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const img = entry.target;
            img.src = img.dataset.src;
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
        if (userMenu) {
            userMenu.classList.remove('hidden');
            const nameEl = userMenu.querySelector('.user-name-trigger');
            if (nameEl) {
                const firstName = user.full_name ? user.full_name.split(' ')[0] : (user.username || 'Account');
                nameEl.innerHTML = `${escapeHTML(firstName)} <i class="ph ph-caret-down"></i>`;
            }
        }
        if (adminLink && API.isAdmin()) {
            adminLink.classList.remove('hidden');
        }
        if (wishlistLink) wishlistLink.classList.remove('hidden');
        updateCartCount();
    } else {
        authLinks.classList.remove('hidden');
        if (userMenu) userMenu.classList.add('hidden');
        if (adminLink) adminLink.classList.add('hidden');
        if (wishlistLink) wishlistLink.classList.add('hidden');
        if (cartCount) cartCount.textContent = '0';
    }

    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-links a').forEach((link) => {
        if (link.getAttribute('href') === currentPath) {
            link.parentElement.classList.add('active');
        } else {
            link.parentElement.classList.remove('active');
        }
    });
}

function updateWishlistBadge() {
    const badge = document.getElementById('wishlist-count');
    if (badge) badge.textContent = window.wishlistItems ? window.wishlistItems.size : 0;
}

async function updateCartCount() {
    try {
        const controller = createRequestController();
        const cart = await API.get('/store/cart/', { signal: controller.signal });
        const badge = document.getElementById('cart-count');
        if (badge && cart) badge.textContent = cart.item_count || '0';
    } catch {
        const badge = document.getElementById('cart-count');
        if (badge) badge.textContent = '0';
    }
}

async function logout() {
    try {
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
    const imgSrc = product.display_image || product.image_url || product.image || '';
    const isOOS = product.stock <= 0;
    const cardOpacity = isOOS ? '0.6' : '1';
    const isInWishlist = window.wishlistItems ? window.wishlistItems.has(product.id) : false;
    const heartIcon = isInWishlist ? '<i class="ph-fill ph-heart"></i>' : '<i class="ph ph-heart"></i>';

    return `
        <div class="card product-card" onclick="window.location.href='/product/${product.slug}/'" data-product-id="${product.id}" style="opacity: ${cardOpacity}; position: relative;">
            <div class="product-card-image">
                <img data-src="${imgSrc}" alt="${escapeHTML(product.name)}" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 400'%3E%3Crect fill='%2316161f' width='400' height='400'/%3E%3C/svg%3E">
                ${discount > 0 && !isOOS ? `<span class="product-card-badge">${discount}% Off</span>` : ''}
                ${isOOS ? `<span class="product-card-badge" style="background:var(--error); color:white;">Out of Stock</span>` : ''}
                <button class="wishlist-btn" onclick="event.stopPropagation(); toggleWishlist(${product.id})" id="btn-wishlist-${product.id}" style="position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.5); color: var(--gold); border: none; border-radius: 50%; width: 36px; height: 36px; font-size: 1.2rem; cursor: pointer; z-index: 2; transition: transform 0.2s; display: flex; align-items: center; justify-content: center;">${heartIcon}</button>
                <div class="product-card-actions">
                    ${isOOS
                        ? `<button class="product-card-action-btn" disabled style="opacity:0.5;cursor:not-allowed;" title="Out of Stock"><i class="ph ph-prohibit"></i></button>`
                        : `<button class="product-card-action-btn" onclick="event.stopPropagation(); addToCart(${product.id})" title="Add to Cart"><i class="ph ph-shopping-cart"></i></button>`}
                </div>
            </div>
            <div class="product-card-info">
                <div class="product-card-category">${escapeHTML(product.category_name || '')}</div>
                <h3 class="product-card-name">${escapeHTML(product.name)}</h3>
                <div class="product-card-price">
                    <span class="current">${formatPrice(product.price)}</span>
                    ${product.compare_price ? `<span class="original">${formatPrice(product.compare_price)}</span>` : ''}
                    ${discount > 0 ? `<span class="discount">${discount}% off</span>` : ''}
                </div>
            </div>
        </div>
    `;
}

async function addToCart(productId, quantity = 1) {
    const user = await API.bootstrapUser();
    if (!user) {
        Toast.info('Please login to add items to cart');
        setTimeout(() => { window.location.href = '/login/'; }, 1000);
        return;
    }

    const response = await API.post('/store/cart/', { product_id: productId, quantity });
    if (response.ok) {
        Toast.success('Added to cart');
        updateCartCount();
    } else {
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
        btn.innerHTML = added ? '<i class="ph-fill ph-heart"></i>' : '<i class="ph ph-heart"></i>';
        btn.style.transform = 'scale(1.2)';
        setTimeout(() => { btn.style.transform = 'none'; }, 200);
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    Toast.init();
    RouteRuntime.installGlobalErrorGuards();
    await updateNavbar();
    await loadWishlistItems();
    initLazyLoading();

    const navbar = document.getElementById('navbar');
    const toggle = document.getElementById('nav-toggle');
    const links = document.getElementById('nav-links');
    const overlay = document.getElementById('menu-overlay');
    const userTrigger = document.querySelector('.user-name-trigger');
    const userMenu = document.getElementById('user-menu');

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

        if (userTrigger && userMenu) {
            userTrigger.addEventListener('click', (event) => {
                if (window.innerWidth <= 768) {
                    event.preventDefault();
                    userMenu.classList.toggle('active');
                }
            });
        }

        links.querySelectorAll('a').forEach((link) => {
            link.addEventListener('click', () => {
                if (link.classList.contains('user-name-trigger')) return;
                if (links.classList.contains('open')) toggleMenu();
            });
        });
    }
});
