/** @odoo-module **/
/**
 * Store Description Tabs + FAQ Accordion + Animations — Woodforest Acceptance
 *
 * Handles:
 *  1. Tab switching (pill navigation)
 *  2. FAQ accordion toggle
 *  3. Scroll-triggered entrance animations (IntersectionObserver)
 *  4. Tab-pane entrance animations (fade-in on tab switch)
 *  5. Hover effects (CTA buttons, terminal image, feature/support cards)
 *
 * Uses whenReady + MutationObserver since Odoo sanitizes data-* attributes
 * from the description HTML. All animations are inline-style based — no
 * <style> blocks or @keyframes needed.
 */

import { whenReady } from "@odoo/owl";

/* ── Tab style constants ──────────────────────────────────────────── */
const ACTIVE_BG = '#FFFFFF';
const ACTIVE_COLOR = '#111111';
const ACTIVE_SHADOW = '0 1px 3px rgba(0,0,0,0.1)';
const INACTIVE_BG = 'transparent';
const INACTIVE_COLOR = '#6B7280';
const INACTIVE_SHADOW = 'none';

/* ── Animation easing constants (from Figma source) ───────────────── */
const EASE_SNAP   = 'cubic-bezier(0.22, 1, 0.36, 1)';
const EASE_SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';
const EASE_SETUP  = 'cubic-bezier(0.25, 0.1, 0.25, 1)';
const EASE_OUT    = 'ease-out';
const EASE_STD    = 'ease';


/* ================================================================
 *  ANIMATION UTILITIES
 * ================================================================ */

/** Set element to hidden state (no transition) */
function hideEl(el) {
    el.style.opacity    = '0';
    el.style.transform  = 'translateY(20px)';
    el.style.transition = 'none';
}

/** Reveal element after a delay with transition */
function revealEl(el, durationMs, delayMs, easing) {
    const dur  = durationMs || 600;
    const del  = delayMs    || 0;
    const ease = easing     || EASE_SNAP;
    setTimeout(() => {
        el.style.transition =
            'opacity ' + dur + 'ms ' + ease + ', ' +
            'transform ' + dur + 'ms ' + ease;
        el.style.opacity   = '1';
        el.style.transform = 'translateY(0)';
    }, del);
}


/* ================================================================
 *  1. TAB SWITCHING
 * ================================================================ */

function initStoreTabs(container) {
    const tabs = container.querySelectorAll('.wf-tab-link');
    const panes = document.querySelectorAll('.wf-tab-pane');

    if (!tabs.length || !panes.length) return;

    tabs.forEach((tab) => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();

            const targetClass = Array.from(tab.classList)
                .find(c => c.startsWith('wf-target-'));
            if (!targetClass) return;

            const paneId = targetClass.replace('wf-target-', '');

            // Deactivate all tabs
            tabs.forEach(t => {
                t.style.backgroundColor = INACTIVE_BG;
                t.style.color = INACTIVE_COLOR;
                t.style.boxShadow = INACTIVE_SHADOW;
                t.classList.remove('active');
                const img = t.querySelector('img');
                if (img) img.style.filter = 'none';
            });

            // Activate clicked tab
            tab.style.backgroundColor = ACTIVE_BG;
            tab.style.color = ACTIVE_COLOR;
            tab.style.boxShadow = ACTIVE_SHADOW;
            tab.classList.add('active');
            const activeImg = tab.querySelector('img');
            if (activeImg) activeImg.style.filter = 'brightness(0)';

            // Hide all panes
            panes.forEach(p => {
                p.style.display = 'none';
                p.classList.remove('show', 'active');
            });

            // Show target pane with entrance animation
            const targetPane = document.querySelector('.wf-pane-' + paneId);
            if (targetPane) {
                targetPane.style.display = 'block';
                targetPane.classList.add('show', 'active');
                animatePaneEntrance(targetPane);
            }
        });
    });
}


/* ================================================================
 *  2. FAQ ACCORDION
 * ================================================================ */

function initFaqAccordion() {
    const items = document.querySelectorAll('.wf-faq-item');
    if (!items.length) return;

    items.forEach((item) => {
        const question = item.querySelector('.wf-faq-question');
        const answer = item.querySelector('.wf-faq-answer');
        const chevron = item.querySelector('.wf-faq-chevron');
        if (!question || !answer) return;

        // Set chevron transition
        if (chevron) {
            chevron.style.transition = 'transform 200ms ' + EASE_STD;
        }

        question.addEventListener('click', () => {
            const isOpen = answer.style.display === 'block';

            // Close all (accordion behavior)
            items.forEach((otherItem) => {
                const otherAnswer = otherItem.querySelector('.wf-faq-answer');
                const otherChevron = otherItem.querySelector('.wf-faq-chevron');
                if (otherAnswer) otherAnswer.style.display = 'none';
                if (otherChevron) otherChevron.style.transform = 'rotate(0deg)';
                otherItem.style.transition = 'background-color 200ms ease, border-radius 200ms ease';
                otherItem.style.backgroundColor = 'transparent';
                otherItem.style.borderRadius = '0';
            });

            // Toggle clicked item
            if (!isOpen) {
                answer.style.display = 'block';
                if (chevron) chevron.style.transform = 'rotate(180deg)';
                item.style.transition = 'background-color 200ms ease, border-radius 200ms ease';
                item.style.backgroundColor = '#FAFAFA';
                item.style.borderRadius = '12px';

                // Fade-in the answer text
                answer.style.opacity = '0';
                answer.style.transition = 'none';
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        answer.style.transition = 'opacity 200ms ' + EASE_OUT;
                        answer.style.opacity = '1';
                    });
                });
            }
        });
    });
}


/* ================================================================
 *  3. SCROLL-TRIGGERED ANIMATIONS
 *     Elements with class "wf-anim-scroll" fade+slide in when
 *     they enter the viewport.
 *     Uses data-wf-delay (seconds) and data-wf-ease attributes.
 * ================================================================ */

function initScrollAnimations() {
    const scrollEls = document.querySelectorAll('.wf-anim-scroll');
    if (!scrollEls.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const el = entry.target;
            const delayS  = parseFloat(el.getAttribute('data-wf-delay') || '0');
            const easeKey = el.getAttribute('data-wf-ease') || 'snap';
            const easing  = easeKey === 'spring' ? EASE_SPRING
                          : easeKey === 'setup'  ? EASE_SETUP
                          : EASE_SNAP;
            revealEl(el, 600, delayS * 1000, easing);
            observer.unobserve(el);
        });
    }, {
        threshold: 0.15,
        rootMargin: '-60px 0px',
    });

    scrollEls.forEach((el) => {
        hideEl(el);
        observer.observe(el);
    });
}


/* ================================================================
 *  4. TAB PANE ENTRANCE ANIMATIONS
 *     Elements with class "wf-anim-enter" inside a tab pane
 *     fade+slide in when the tab is shown.
 * ================================================================ */

function animatePaneEntrance(pane) {
    if (!pane) return;
    const enterEls = pane.querySelectorAll('.wf-anim-enter');
    enterEls.forEach((el) => {
        const delayS = parseFloat(el.getAttribute('data-wf-delay') || '0');
        // Reset to hidden
        el.style.transition = 'none';
        el.style.opacity    = '0';
        el.style.transform  = 'translateY(20px)';
        // Reveal
        revealEl(el, 600, 50 + delayS * 1000, EASE_SNAP);
    });

    // Re-arm scroll animations inside the pane
    const scrollEls = pane.querySelectorAll('.wf-anim-scroll');
    if (scrollEls.length) {
        const obs = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                const el = entry.target;
                const delayS  = parseFloat(el.getAttribute('data-wf-delay') || '0');
                const easeKey = el.getAttribute('data-wf-ease') || 'snap';
                const easing  = easeKey === 'spring' ? EASE_SPRING
                              : easeKey === 'setup'  ? EASE_SETUP
                              : EASE_SNAP;
                revealEl(el, 600, delayS * 1000, easing);
                obs.unobserve(el);
            });
        }, { threshold: 0.15, rootMargin: '-60px 0px' });

        scrollEls.forEach((el) => {
            if (parseFloat(el.style.opacity) < 1) {
                hideEl(el);
                obs.observe(el);
            }
        });
    }
}


/* ================================================================
 *  5. HOVER EFFECTS — OVERVIEW
 * ================================================================ */

function initOverviewHovers() {
    // CTA buttons (hero + bottom)
    document.querySelectorAll('.wf-hover-cta').forEach((btn) => {
        btn.style.transition =
            'background-color 300ms ease, transform 300ms ease, box-shadow 300ms ease';
        btn.addEventListener('mouseenter', () => {
            btn.style.backgroundColor = '#3d7e1e';
            btn.style.transform       = 'translateY(-2px)';
            btn.style.boxShadow       = '0 14px 36px rgba(49,107,24,0.16)';
        });
        btn.addEventListener('mouseleave', () => {
            btn.style.backgroundColor = '#316B18';
            btn.style.transform       = 'translateY(0)';
            btn.style.boxShadow       = '0 10px 30px rgba(49,107,24,0.12)';
        });
    });

    // Terminal hero image hover
    const terminalImg = document.querySelector('.wf-hover-terminal');
    if (terminalImg) {
        terminalImg.style.transition = 'transform 500ms ease, filter 500ms ease';
        terminalImg.addEventListener('mouseenter', () => {
            terminalImg.style.transform = 'scale(1.08)';
            terminalImg.style.filter    = 'drop-shadow(0 40px 80px rgba(0,0,0,0.18))';
        });
        terminalImg.addEventListener('mouseleave', () => {
            terminalImg.style.transform = 'scale(1)';
            terminalImg.style.filter    = 'drop-shadow(0 30px 60px rgba(0,0,0,0.15))';
        });
    }
}


/* ================================================================
 *  6. HOVER EFFECTS — FEATURE CARDS
 * ================================================================ */

function initFeatureCards() {
    document.querySelectorAll('.wf-hover-feature').forEach((card) => {
        card.style.transition = 'box-shadow 300ms ease, transform 300ms ease';
        card.addEventListener('mouseenter', () => {
            card.style.transform  = 'translateY(-2px)';
            card.style.boxShadow  =
                '0 4px 12px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.03)';
        });
        card.addEventListener('mouseleave', () => {
            card.style.transform  = 'translateY(0)';
            card.style.boxShadow  =
                '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02)';
        });
    });
}


/* ================================================================
 *  7. HOVER EFFECTS — SUPPORT CARDS & BUTTONS
 * ================================================================ */

function initSupportCards() {
    // Card hover lift
    document.querySelectorAll('.wf-hover-support').forEach((card) => {
        card.style.transition = 'box-shadow 300ms ease, transform 300ms ease';
        card.addEventListener('mouseenter', () => {
            card.style.transform = 'translateY(-2px)';
            card.style.boxShadow = '0 6px 20px rgba(0,0,0,0.08), 0 2px 6px rgba(0,0,0,0.04)';
        });
        card.addEventListener('mouseleave', () => {
            card.style.transform = 'translateY(0)';
            card.style.boxShadow = 'none';
        });
    });

    // Filled green button hover
    document.querySelectorAll('.wf-hover-btn-filled').forEach((btn) => {
        btn.style.transition =
            'background-color 300ms ease, box-shadow 300ms ease';
        btn.addEventListener('mouseenter', () => {
            btn.style.backgroundColor = '#53B628';
            btn.style.boxShadow       = '0 4px 16px rgba(49,107,24,0.25)';
        });
        btn.addEventListener('mouseleave', () => {
            btn.style.backgroundColor = '#316B18';
            btn.style.boxShadow       = '0 2px 8px rgba(49,107,24,0.15)';
        });
    });

    // Outline button hover
    document.querySelectorAll('.wf-hover-btn-outline').forEach((btn) => {
        btn.style.transition =
            'background-color 300ms ease, border-color 300ms ease, color 300ms ease';
        btn.addEventListener('mouseenter', () => {
            btn.style.backgroundColor = 'rgba(49,107,24,0.04)';
            btn.style.borderColor     = '#316B18';
            btn.style.color           = '#316B18';
        });
        btn.addEventListener('mouseleave', () => {
            btn.style.backgroundColor = 'transparent';
            btn.style.borderColor     = '#2D5E15';
            btn.style.color           = '#2D5E15';
        });
    });
}


/* ================================================================
 *  8. SETUP STEPS — Progressive scroll completion
 *     Each step starts gray with a number. As each row enters the
 *     viewport, the circle turns green, the number fades out,
 *     the checkmark fades in, and the content slides up.
 * ================================================================ */

function initSetupSteps() {
    const rows = document.querySelectorAll('.wf-step-row');
    if (!rows.length) return;

    const stepObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const row = entry.target;

            // Connector line → green
            const line = row.querySelector('.wf-step-line');
            if (line) {
                line.style.backgroundColor = '#316B18';
            }

            // Circle → green fill
            const circle = row.querySelector('.wf-step-circle');
            if (circle) {
                circle.style.backgroundColor = '#316B18';
                circle.style.borderColor = '#316B18';
            }

            // Number → fade out
            const num = row.querySelector('.wf-step-num');
            if (num) {
                num.style.opacity = '0';
            }

            // Checkmark → fade in (100ms delay)
            const check = row.querySelector('.wf-step-check');
            if (check) {
                setTimeout(() => {
                    check.style.opacity = '1';
                }, 100);
            }

            // Content → slide up + full opacity
            const content = row.querySelector('.wf-step-content');
            if (content) {
                content.style.opacity = '1';
                content.style.transform = 'translateY(0)';
            }

            stepObserver.unobserve(row);
        });
    }, {
        threshold: 0.6,
        rootMargin: '-60px 0px -60px 0px',
    });

    rows.forEach((row) => {
        stepObserver.observe(row);
    });
}


/* ================================================================
 *  INIT — Run all initializers
 * ================================================================ */

function initAll(container) {
    initStoreTabs(container);
    initFaqAccordion();
    initScrollAnimations();
    initSetupSteps();
    initOverviewHovers();
    initFeatureCards();
    initSupportCards();

    // Animate the default active pane on first load
    const activePane = document.querySelector('.wf-tab-pane.active');
    if (activePane) {
        animatePaneEntrance(activePane);
    }
}

whenReady(() => {
    const existing = document.querySelector('.wf-store-tabs');
    if (existing) {
        initAll(existing);
        return;
    }

    const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType !== 1) continue;
                const found = node.querySelector?.('.wf-store-tabs')
                    || (node.classList?.contains('wf-store-tabs') ? node : null);
                if (found) {
                    initAll(found);
                    observer.disconnect();
                    return;
                }
            }
        }
    });

    observer.observe(document.body, { childList: true, subtree: true });
});
