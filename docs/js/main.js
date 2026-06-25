// Theme toggle (persisted) + mobile nav. Mirrors the provabl.dev behavior.
(function () {
    var root = document.documentElement;
    var toggle = document.getElementById('theme-toggle');
    var icon = document.getElementById('theme-icon');

    function setIcon() {
        var dark = root.getAttribute('data-theme') === 'dark';
        if (icon) icon.textContent = dark ? '☀️' : '🌙';
    }
    setIcon();

    if (toggle) {
        toggle.addEventListener('click', function () {
            var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            root.setAttribute('data-theme', next);
            try { localStorage.setItem('theme', next); } catch (e) {}
            setIcon();
        });
    }

    // Mobile hamburger
    var burger = document.getElementById('hamburger-btn');
    var links = document.getElementById('nav-links');
    if (burger && links) {
        burger.addEventListener('click', function () {
            var open = links.classList.toggle('open');
            burger.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        // close menu after following an in-page link
        links.querySelectorAll('a').forEach(function (a) {
            a.addEventListener('click', function () {
                links.classList.remove('open');
                burger.setAttribute('aria-expanded', 'false');
            });
        });
    }
})();
