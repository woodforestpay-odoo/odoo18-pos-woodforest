// Minimal stub so Odoo recognises Woodforest as a payment terminal
import models, { PaymentTerminal } from "@point_of_sale/app/models";

class Woodforest extends PaymentTerminal {
  // No special behaviour; actual flow is handled in payment_screen.js
  async startPayment(line) {
    // simply return the line so the existing flow continues
    return line;
  }
}

models.register_payment_terminal("woodforest", Woodforest);
