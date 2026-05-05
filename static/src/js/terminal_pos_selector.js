/** @odoo-module **/

/**
 * Custom inline POS selector widget for the Terminal list view.
 *
 * Renders directly in the readonly list as a clickable element:
 * - 1 click opens a native <select> dropdown (no text input, no search).
 * - If session is busy (open), shows a lock icon + tooltip instead.
 * - Saves immediately on change via ORM, then reloads the list.
 *
 * This avoids editable="bottom" entirely, preserving row-click navigation
 * to the terminal form view.
 */

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart, onWillUpdateProps } from "@odoo/owl";

class TerminalPosSelector extends Component {
  static template = "pos_woodforest.TerminalPosSelector";
  static props = {
    // Standard field widget props from Odoo
    record: { type: Object },
    name: { type: String },
    readonly: { type: Boolean, optional: true },
    // Allow any other prop from Odoo
    "*": true,
  };

  setup() {
    this.orm = useService("orm");
    this.notification = useService("notification");
    this.action = useService("action");

    this.state = useState({
      options: [],
      loading: false,
    });

    onWillStart(() => this._loadOptions());
    onWillUpdateProps(() => this._loadOptions());
  }

  get record() {
    return this.props.record;
  }

  get currentPosId() {
    const val = this.record.data.pos_config_id;
    return val ? val[0] : false;
  }

  get currentPosName() {
    const val = this.record.data.pos_config_id;
    return val ? val[1] : "";
  }

  get isBusy() {
    return this.record.data.session_status === "busy";
  }

  async _loadOptions() {
    // Find POS configs WITHOUT an open session
    const openSessions = await this.orm.searchRead(
      "pos.session",
      [["state", "in", ["opened", "opening_control"]]],
      ["config_id"],
    );
    const blockedIds = openSessions.map((s) => s.config_id[0]);

    const allConfigs = await this.orm.searchRead(
      "pos.config",
      [["id", "not in", blockedIds]],
      ["id", "name"],
    );

    this.state.options = allConfigs;
  }

  onSelectClick(ev) {
    // Stop row-click propagation so we don't navigate to the form
    ev.stopPropagation();

    if (this.isBusy) {
      // Don't open — the tooltip handles the message
      return;
    }
  }

  async onSelectChange(ev) {
    ev.stopPropagation();

    const newVal = parseInt(ev.target.value) || false;
    const terminalId = this.record.resId;

    if (newVal === this.currentPosId) return;

    this.state.loading = true;
    try {
      await this.orm.write("payrillium.terminal", [terminalId], {
        pos_config_id: newVal,
      });
      this.notification.add("Assignment updated.", { type: "success" });
      // Reload list to reflect changes
      await this.record.model.load();
    } catch (e) {
      this.notification.add(e.message || "Failed to update assignment.", {
        type: "danger",
      });
      // Revert the select to previous value
      ev.target.value = this.currentPosId || "";
    }
    this.state.loading = false;
  }
}

TerminalPosSelector.template = "pos_woodforest.TerminalPosSelector";

registry.category("fields").add("terminal_pos_selector", {
  component: TerminalPosSelector,
  supportedTypes: ["many2one"],
});
