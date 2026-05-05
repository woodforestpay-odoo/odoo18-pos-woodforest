/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { LoadingIndicator } from "@web/webclient/loading_indicator/loading_indicator";

patch(LoadingIndicator.prototype, {
  setup() {
    super.setup();
    this._loaderTimer = null;
    this._overlayVisible = false;

    this.env.bus.addEventListener("RPC:REQUEST", () => {
      if (!this._loaderTimer) {
        this._loaderTimer = setTimeout(() => {
          this._showOverlay();
          this._loaderTimer = null;
        }, 500);
      }
    });

    this.env.bus.addEventListener("RPC:RESPONSE", () => {
      if (this._loaderTimer) {
        clearTimeout(this._loaderTimer);
        this._loaderTimer = null;
      }
      this._hideOverlay();
    });
  },

  _showOverlay() {
    if (this._overlayVisible) return;
    const existing = document.getElementById("fullscreen_loader_overlay");
    if (!existing) {
      const loader = document.createElement("div");
      loader.id = "fullscreen_loader_overlay";
      loader.className = "fullscreen-loader";
      loader.innerHTML = `
 <div class="spinner"></div>
 <div class="loading-text">Please wait...</div>
 <button id="payrillium_cancel_btn"
 style="margin-top:18px; padding:10px 28px; background:#e53935;
 color:#fff; border:none; border-radius:8px; font-size:15px; font-weight:600;
 cursor:pointer; letter-spacing:0.5px; box-shadow:0 2px 8px rgba(0,0,0,0.25);"
 onclick="window.__payrilliumAbort && window.__payrilliumAbort()">
 Cancel
 </button>
 `;
      document.body.appendChild(loader);
    }
    this._overlayVisible = true;
  },

  _hideOverlay() {
    const existing = document.getElementById("fullscreen_loader_overlay");
    if (existing) {
      existing.remove();
    }
    this._overlayVisible = false;
  },
});
