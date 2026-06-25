# Pay by Link – Flujo completo y punto de recuperación (NO SUBIR A GIT)

**Uso:** Documento de referencia y punto de recuperación si el flujo Pay by Link se rompe. Contiene el flujo tal como funciona hoy (cache, chatter, notificaciones) para poder restaurarlo o comparar.

---

## 1. Resumen del flujo (usuario)

1. Usuario abre una **factura** (account.move) en Facturación.
2. Lanza la acción **“Generar enlace de pago”** (wizard estándar Odoo payment/account_payment).
3. Se abre el wizard con importe y botón **“Generate and Copy Payment Link”**.
4. Al hacer **clic** en ese botón:
   - Si es enlace Odoo (portal o /payment/pay): se llama al backend Payrillium, se obtiene o reutiliza el enlace Mirillium, se copia al portapapeles, se muestra notificación y (si aplica) se escribe en el chatter.
   - Si es enlace Payrillium/Cybersource ya generado: se copia directo y se muestra notificación.
   - Si hay **caché** (misma factura + mismo importe en la sesión): no se llama al servidor, se usa la URL cacheada y se muestra “(cached)”.

---

## 2. Archivos implicados

| Rol                    | Archivo                                                    | Qué hace                                                                                                                        |
| ---------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Patch del botón Copiar | `static/src/js/payment_link_wizard.js`                     | Intercepta el clic en CopyButton del wizard; detecta tipo de URL; usa caché; llama RPC; muestra notificaciones.                 |
| Ruta y chatter         | `controllers/main.py` → `generate_link`                    | Valida, busca enlace existente, llama `create_payment_link`, escribe en chatter, devuelve `{ success, link, warning? }`.        |
| Copiar desde chatter   | `static/src/js/chatter_payment_link.js`                    | Patch de `Message.copyLink`: si el mensaje es “Payment link created” con URL Cybersource, copia esa URL y muestra notificación. |
| Vista del wizard       | `views/accounting_invoicing_payment_link_wizard_patch.xml` | Solo hace el campo `amount` readonly. El campo `link` usa el widget estándar `PaymentWizardCopyClipboardButtonField`.           |
| Assets backend         | `__manifest__.py` → `web.assets_backend`                   | Incluye `payment_link_wizard.js` y `chatter_payment_link.js` (y si aplica `utils.js` antes de los que lo usan).                 |

---

## 3. Flujo detallado (payment_link_wizard.js)

### 3.1 Setup

- Se hace **patch** de `CopyButton.prototype` (componente de Odoo `@web/core/copy_button/copy_button`).
- En `setup()`: se llama a `super.setup()` y se inyectan `useService("orm")` y `useService("notification")` (this.orm, this.notification).  
  **Nota:** En contextos donde no exista el servicio (p. ej. wizard en diálogo), esto puede lanzar; en ese caso se suele evitar `useService` en setup y leer `this.env?.services?.notification` en `onClick`.

### 3.2 onClick – decisión por tipo de contenido

- **content** = `this.props.content` (valor del campo del wizard, normalmente la URL).
- Si no es string o está vacío → `return super.onClick()` (comportamiento por defecto).
- Detección de tipo de enlace:
  - **Enlace Odoo:** `content.includes("/payment/pay")` **o** `content.includes("move_id=")` (portal facturas).
  - **Enlace Payrillium ya generado:** `content.includes("/payByLink/pay")` **o** `content.includes("ebc.cybersource.com")`.
- Si **no** es ninguno → `return super.onClick()`.

### 3.3 Si es enlace Payrillium/Cybersource (ya generado)

- Se copia `content` al portapapeles.
- `this.notification.add("Payment link copied", { type: "success" })`.
- return (no se llama al backend).

### 3.4 Si es enlace Odoo (generar/reutilizar vía Payrillium)

1. **Parsear URL:** `new URL(content, window.location.origin)`.
2. **Parámetros:** `amount = url.searchParams.get("amount")`, `invoiceId = url.searchParams.get("move_id") || url.searchParams.get("invoice_id")`.
3. Si faltan `amount` o `invoiceId` → `return super.onClick()`.
4. **Caché:** clave `cacheKey = \`${invoiceId}_${amount}\``.
   - Si `generatedLinksCache.has(cacheKey)`:
     - Se obtiene la URL cacheada, se copia al portapapeles.
     - `this.notification.add("Payment link copied (cached)", { type: "info" })`.
     - return (no RPC).
5. **RPC:** `rpcRequest("/woodforest/generate_link", { model: "account.move", id: invoiceId, amount: parseFloat(amount) })`.
6. **Respuesta:**
   - Si `!resp` → notificación danger "Failed to generate link: empty response", return.
   - Si `resp.success !== true` → notificación danger con `resp.error`, return.
   - Si `!resp.link` → notificación danger "Server reported success but returned no link", return.
7. **Éxito:** `generatedLinksCache.set(cacheKey, newLink)`, copiar `newLink` al portapapeles.
8. **Notificación final:**
   - Si `resp.warning` → `this.notification.add(resp.warning, { type: "warning" })`.
   - Si no → `this.notification.add("Payment link copied", { type: "success" })`.
9. **Catch:** cualquier excepción → `this.notification.add("Failed to generate/copy link", { type: "danger" })`.

### 3.5 Caché (generatedLinksCache)

- **Tipo:** `Map()` en memoria (variable de módulo en `payment_link_wizard.js`).
- **Clave:** `"${invoiceId}_${amount}"` (ej. `"42_150.00"`).
- **Valor:** URL completa del enlace Payrillium/Mirillium.
- **Alcance:** Solo la pestaña/sesión actual; se pierde al recargar o cerrar.
- **Objetivo:** Evitar repetir la petición a `/woodforest/generate_link` para el mismo par factura+importe en la misma sesión.

---

## 4. Flujo en el backend (generate_link)

- **Ruta:** `POST /woodforest/generate_link` (JSON), `auth='user'`.
- **Parámetros:** `model`, `id`, `amount`. Solo se permite `model='account.move'`.
- **Validaciones:** id entero > 0, amount float > 0, registro existe, permisos de lectura.
- **Reutilización:** Se buscan enlaces existentes en `payrillium.payment.link` para esa factura (`invoice_id`) con `status in ('active','pending')`. Si hay alguno con `link_url`, se devuelve `{ success: True, link: existing_link.link_url, warning: "A payment link already exists..." }` sin llamar a la API externa.
- **Creación:** Si no se reutiliza, se llama a `create_payment_link(record, amount=amount)` (Mirillium). Si devuelve dict con `error` (p. ej. DUPLICATE_RECORD), se trata y puede devolverse enlace existente o error.
- **Chatter:** Si hay `link` válido (nuevo o reutilizado) y el registro tiene `message_post`:
  - Se construye un cuerpo con URL truncada (primeros 30 caracteres + "...") y la URL completa en `<span style="display:none;">...</span>` (escapada).
  - `record.sudo().message_post(body=..., message_type='notification', subtype_xmlid='mail.mt_comment')`.
- **Respuesta:** `{ "success": True, "link": link }` y opcionalmente `"warning"`.

---

## 5. Chatter (chatter_payment_link.js)

- **Patch:** `Message.prototype.copyLink` (modelo de mensaje del chatter, módulo mail).
- **Condición:** El mensaje tiene `body` que incluye `"Payment link created"` y una URL que coincide con el patrón (ej. `https://ebctest.cybersource.com...`).
- **Comportamiento:** Se copia esa URL al portapapeles y se muestra notificación "Payment Link Copied!" (o error si falla la copia). Si no coincide, se llama a `super.copyLink()`.
- **Nota:** El **texto** “Payment link created” y la URL en el cuerpo los escribe el controlador en `generate_link` (message_post). Este JS solo afecta al **copiar** desde un mensaje ya existente en el chatter.

---

## 6. Notificaciones (toast)

- **Origen:** Siempre desde el front (payment_link_wizard.js o chatter_payment_link.js) con `this.notification.add(...)` o `this.store.env.services.notification.add(...)`.
- **Tipos usados:** `success`, `info` (cached), `warning` (enlace reutilizado), `danger` (errores).
- **Momento:** Solo al hacer **clic** en el botón de copiar (o al copiar desde el chatter), no al abrir el wizard.
- **Requisito:** El servicio de notificaciones debe estar disponible en el entorno donde se renderiza el CopyButton; si no, el patch debe evitar depender de él en setup (p. ej. leer en onClick desde `this.env?.services?.notification`) para no romper.

---

## 7. Checklist de recuperación (si algo se rompe)

- [ ] `payment_link_wizard.js` está en `web.assets_backend` y carga sin errores (sin “module not defined”, sin “useService already declared”).
- [ ] Import de CopyButton: `@web/core/copy_button/copy_button` (Odoo 18).
- [ ] Detección de enlace: `/payment/pay` **o** `move_id=` para enlace Odoo; `/payByLink/pay` o `ebc.cybersource.com` para enlace Payrillium ya generado.
- [ ] Caché: `generatedLinksCache` Map con clave `invoiceId_amount`; si existe, copiar y notificación “(cached)” sin RPC.
- [ ] RPC: `/woodforest/generate_link` con `model: "account.move", id: invoiceId, amount: parseFloat(amount)`.
- [ ] Tras éxito: guardar en caché, copiar al portapapeles, notificación success o warning según `resp.warning`.
- [ ] Controlador: valida modelo/id/amount, busca enlaces existentes, llama `create_payment_link`, en caso de éxito hace `record.message_post(...)` para el chatter y devuelve `{ success: True, link }`.
- [ ] Chatter: mensaje con “Payment link created”, URL truncada visible y URL completa en span oculto; chatter_payment_link.js parchea copyLink para esos mensajes.

---

## 8. Versión de referencia (fragmentos clave)

### payment_link_wizard.js – lógica de decisión y caché

- Patch `CopyButton.prototype`, `setup()` con orm y notification.
- onClick: si no string → super.onClick(). Si no es payment link (Odoo ni Payrillium) → super.onClick(). Si Payrillium link → copiar y notificación success. Si Odoo link → parsear amount e invoiceId; si faltan → super.onClick(); si hay cacheKey en generatedLinksCache → copiar cache y notificación info “(cached)”; si no → RPC generate_link; según respuesta, notificación danger/warning/success y en éxito guardar en caché y copiar.

### controllers/main.py – generate_link

- Validar model (solo account.move), id, amount. Buscar existing_links por invoice_id (y por patrón INV{id}A si hace falta). Si hay link con link_url → return success + link + warning. Si no, create_payment_link(record, amount). Si link es dict con error (DUPLICATE_RECORD etc.) manejar. Si link válido y record tiene message_post → message_post con body “Payment link created: ” + short_url + hidden full URL; return success + link.

---

_Documento local de recuperación. No subir a git._
