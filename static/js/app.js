/* ═══════════════════════════════════════════════════════════════
   Iri Collections — Core JavaScript
   JWT Auth, API Client, Cart Management, Lazy Loading
   ═══════════════════════════════════════════════════════════════ */

// ─── Utility ───────────────────────────────────────────────────
function escapeHTML(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ─── API Client ────────────────────────────────────────────────
const API = {
    base: '/api',

    getToken() {
        return localStorage.getItem('access_token');
    },

    setTokens(tokens) {
        localStorage.setItem('access_token', tokens.access);
        localStorage.setItem('refresh_token', tokens.refresh);
    },

    clearTokens() {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('user');
    },

    getUser() {
        const u = localStorage.getItem('user');
        return u ? JSON.parse(u) : null;
    },

    setUser(user) {
        localStorage.setItem('user', JSON.stringify(user));
    },

    isLoggedIn() {
        return !!this.getToken();
    },

    isAdmin() {
        const user = this.getUser();
        return user && user.role === 'admin';
    },

    async refreshToken() {
        const refresh = localStorage.getItem('refresh_token');
        if (!refresh) return false;
        try {
            const res = await fetch(`${this.base}/auth/refresh/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh })
            });
            if (!res.ok) return false;
            const data = await res.json();
            localStorage.setItem('access_token', data.access);
            if (data.refresh) localStorage.setItem('refresh_token', data.refresh);
            return true;
        } catch { return false; }
    },

    async request(endpoint, options = {}) {
        const url = `${this.base}${endpoint}`;
        const headers = { ...options.headers };

        if (!(options.body instanceof FormData)) {
            headers['Content-Type'] = 'application/json';
        }

        const token = this.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;

        let res = await fetch(url, { ...options, headers });

        // Auto-refresh on 401
        if (res.status === 401 && token) {
            const refreshed = await this.refreshToken();
            if (refreshed) {
                headers['Authorization'] = `Bearer ${this.getToken()}`;
                res = await fetch(url, { ...options, headers });
            } else {
                this.clearTokens();
                window.location.href = '/login/';
                return null;
            }
        }
        return res;
    },

    async get(endpoint) {
        const res = await this.request(endpoint);
        if (!res) return null;
        return res.json();
    },

    async post(endpoint, data) {
        const res = await this.request(endpoint, {
            method: 'POST',
            body: JSON.stringify(data)
        });
        if (!res) return null;
        return { ok: res.ok, status: res.status, data: await res.json() };
    },

    async patch(endpoint, data) {
        const res = await this.request(endpoint, {
            method: 'PATCH',
            body: JSON.stringify(data)
        });
        if (!res) return null;
        return { ok: res.ok, status: res.status, data: await res.json() };
    },

    async delete(endpoint, data = null) {
        const opts = { method: 'DELETE' };
        if (data) opts.body = JSON.stringify(data);
        const res = await this.request(endpoint, opts);
        if (!res) return null;
        try { return { ok: res.ok, data: await res.json() }; }
        catch { return { ok: res.ok, data: null }; }
    },
};

// ─── Toast Notifications ───────────────────────────────────────
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

    success(msg) { this.show(msg, 'success'); },
    error(msg) { this.show(msg, 'error'); },
    info(msg) { this.show(msg, 'info'); }
};

// ─── Lazy Loading ──────────────────────────────────────────────
function initLazyLoading() {
    const images = document.querySelectorAll('img[data-src]');
    if (!images.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target;
                img.src = img.dataset.src;
                img.removeAttribute('data-src');
                img.addEventListener('load', () => img.classList.add('loaded'));
                observer.unobserve(img);
            }
        });
    }, { rootMargin: '100px' });

    images.forEach(img => observer.observe(img));
}

// ─── Update Nav State ──────────────────────────────────────────
function updateNavbar() {
    const authLinks = document.getElementById('auth-links');
    const userMenu = document.getElementById('user-menu');
    const cartCount = document.getElementById('cart-count');
    const adminLink = document.getElementById('admin-link');
    const wishlistLink = document.getElementById('wishlist-link');

    if (!authLinks) return;

    if (API.isLoggedIn()) {
        const user = API.getUser();
        authLinks.classList.add('hidden');
        if (userMenu) {
            userMenu.classList.remove('hidden');
            const nameEl = userMenu.querySelector('.user-name-trigger');
            if (nameEl) {
                const firstName = user?.full_name ? user.full_name.split(' ')[0] : (user?.username || 'Account');
                nameEl.innerHTML = `${firstName} <i class="ph ph-caret-down"></i>`;
            }
        }
        if (adminLink && API.isAdmin()) {
            adminLink.classList.remove('hidden');
        }
        if (wishlistLink) wishlistLink.classList.remove('hidden');
        // Load cart count
        updateCartCount();
    } else {
        authLinks.classList.remove('hidden');
        if (userMenu) userMenu.classList.add('hidden');
        if (adminLink) adminLink.classList.add('hidden');
        if (wishlistLink) wishlistLink.classList.add('hidden');
        if (cartCount) cartCount.textContent = '0';
    }
    
    // Set active link based on current path
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-links a').forEach(link => {
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
    if (!API.isLoggedIn()) return;
    try {
        const cart = await API.get('/store/cart/');
        const badge = document.getElementById('cart-count');
        if (badge && cart) badge.textContent = cart.item_count || '0';
    } catch {}
}

function logout() {
    API.clearTokens();
    Toast.success('Logged out successfully');
    setTimeout(() => window.location.href = '/', 500);
}

// ─── Format Currency ───────────────────────────────────────────
function formatPrice(price) {
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(price);
}

// ─── Product Card HTML ─────────────────────────────────────────
function productCardHTML(product) {
    const discount = product.compare_price
        ? Math.round((1 - product.price / product.compare_price) * 100)
        : 0;
    const imgSrc = product.display_image || product.image_url || product.image || '';

    const isOOS = product.stock <= 0;
    const cardOpacity = isOOS ? '0.6' : '1';
    
    // Heart icon logic
    const isInWishlist = window.wishlistItems ? window.wishlistItems.has(product.id) : false;
    const heartIcon = isInWishlist ? '<i class="ph-fill ph-heart"></i>' : '<i class="ph ph-heart"></i>';

    return `
        <div class="card product-card" onclick="window.location.href='/product/${product.slug}/'" data-product-id="${product.id}" style="opacity: ${cardOpacity}; position: relative;">
            <div class="product-card-image">
                <img data-src="${imgSrc}" alt="${product.name}" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 400'%3E%3Crect fill='%2316161f' width='400' height='400'/%3E%3C/svg%3E">
                ${discount > 0 && !isOOS ? `<span class="product-card-badge">${discount}% Off</span>` : ''}
                ${isOOS ? `<span class="product-card-badge" style="background:var(--error); color:white;">Out of Stock</span>` : ''}
                
                <button class="wishlist-btn" onclick="event.stopPropagation(); toggleWishlist(${product.id})" id="btn-wishlist-${product.id}" style="position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.5); color: var(--gold); border: none; border-radius: 50%; width: 36px; height: 36px; font-size: 1.2rem; cursor: pointer; z-index: 2; transition: transform 0.2s; display: flex; align-items: center; justify-content: center;">${heartIcon}</button>

                <div class="product-card-actions">
                    ${isOOS ? 
                        `<button class="product-card-action-btn" disabled style="opacity:0.5;cursor:not-allowed;" title="Out of Stock"><i class="ph ph-prohibit"></i></button>` : 
                        `<button class="product-card-action-btn" onclick="event.stopPropagation(); addToCart(${product.id})" title="Add to Cart"><i class="ph ph-shopping-cart"></i></button>`
                    }
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

// ─── Add to Cart ───────────────────────────────────────────────
async function addToCart(productId, quantity = 1) {
    if (!API.isLoggedIn()) {
        Toast.info('Please login to add items to cart');
        setTimeout(() => window.location.href = '/login/', 1000);
        return;
    }
    const res = await API.post('/store/cart/', { product_id: productId, quantity });
    if (res && res.ok) {
        Toast.success('Added to cart!');
        updateCartCount();
    } else {
        Toast.error(res?.data?.error || 'Failed to add to cart');
    }
}

// ─── Wishlist ──────────────────────────────────────────────────
window.wishlistItems = new Set();
async function loadWishlistItems() {
    if (!API.isLoggedIn()) return;
    try {
        const items = await API.get('/store/wishlist/');
        if (items) {
            window.wishlistItems = new Set(items.map(i => i.id));
            updateWishlistBadge();
        }
    } catch {}
}

async function toggleWishlist(productId) {
    if (!API.isLoggedIn()) {
        Toast.info('Please login to save to wishlist');
        setTimeout(() => window.location.href = '/login/', 1000);
        return;
    }
    const isWished = window.wishlistItems.has(productId);
    try {
        if (isWished) {
            const res = await API.delete('/store/wishlist/', { product_id: productId });
            if (res && res.ok) {
                window.wishlistItems.delete(productId);
                updateWishlistUI(productId, false);
                updateWishlistBadge();
                Toast.success('Removed from wishlist');
            }
        } else {
            const res = await API.post('/store/wishlist/', { product_id: productId });
            if (res && res.ok) {
                window.wishlistItems.add(productId);
                updateWishlistUI(productId, true);
                updateWishlistBadge();
                Toast.success('Added to wishlist');
            }
        }
    } catch {
        Toast.error("Failed to update wishlist");
    }
}

function updateWishlistUI(productId, added) {
    document.querySelectorAll(`#btn-wishlist-${productId}`).forEach(btn => {
        btn.innerHTML = added ? '<i class="ph-fill ph-heart"></i>' : '<i class="ph ph-heart"></i>';
        btn.style.transform = 'scale(1.2)';
        setTimeout(() => btn.style.transform = 'none', 200);
    });
}

// ─── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    Toast.init();
    updateNavbar();
    await loadWishlistItems();
    initLazyLoading();

    const navbar = document.getElementById('navbar');
    const toggle = document.getElementById('nav-toggle');
    const links = document.getElementById('nav-links');
    const overlay = document.getElementById('menu-overlay');
    const userTrigger = document.querySelector('.user-name-trigger');
    const userMenu = document.getElementById('user-menu');

    // Scroll Effect
    const handleScroll = () => {
        if (window.scrollY > 20) {
            navbar.classList.add('scrolled');
        } else {
            navbar.classList.remove('scrolled');
        }
    };
    window.addEventListener('scroll', handleScroll);
    handleScroll(); // Check once on load

    // Mobile Menu Toggle
    if (toggle && links && overlay) {
        const toggleMenu = () => {
            const isOpen = links.classList.toggle('open');
            toggle.classList.toggle('open');
            overlay.classList.toggle('active');
            document.body.style.overflow = isOpen ? 'hidden' : '';
        };

        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleMenu();
        });

        overlay.addEventListener('click', toggleMenu);

        // Mobile User Dropdown Toggle
        if (userTrigger) {
            userTrigger.addEventListener('click', (e) => {
                if (window.innerWidth <= 768) {
                    e.preventDefault();
                    userMenu.classList.toggle('active');
                }
            });
        }

        // Close on link click
        links.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', (e) => {
                if (link.classList.contains('user-name-trigger')) return;
                if (links.classList.contains('open')) toggleMenu();
            });
        });
    }
});
