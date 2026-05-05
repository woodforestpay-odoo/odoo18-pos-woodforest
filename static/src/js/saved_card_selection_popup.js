/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class SavedCardSelectionPopup extends Component {
  static template = "pos_woodforest.SavedCardSelectionPopup";
  static components = { Dialog };
  static props = {
    title: { type: String, optional: true },
    list: { type: Array, optional: true },
    newCardLabel: { type: String, optional: true },
    getPayload: Function,
    close: Function,
  };
  static defaultProps = {
    title: _t("Select a saved card"),
    list: [],
    newCardLabel: _t("Pay with another / new card"),
  };

  setup() {
    this.state = useState({
      selectedId: this.props.list.find((item) => item.isSelected)?.id || null,
    });
    this.selectItem = this.selectItem.bind(this);
  }

  selectItem(itemId) {
    this.state.selectedId = itemId;
    this.confirm();
  }

  confirm() {
    const selected = this.props.list.find(
      (item) => this.state.selectedId === item.id,
    );
    // If a card was selected, return its `item` (the token object).
    // If nothing selected (should not happen via UI), treat as "no token".
    const payload = selected ? selected.item : null;
    this.props.getPayload(payload);
    this.props.close();
  }

  onUseNewCard() {
    // Explicit action: user wants to pay with another / new card,
    // not with any saved token.
    this.props.getPayload(null);
    this.props.close();
  }
}
