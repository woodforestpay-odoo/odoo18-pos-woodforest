# Plan: Renombrar Payrillium → Woodforest

Este documento describe la **mejor forma** de hacer el cambio de nombre del proyecto (payrillium → woodforest), qué implica y cómo hacerlo de forma segura.

---

## 1. Qué hay que cambiar (tipos de ocurrencias)

| Tipo                            | Ejemplo actual                                                | Cambio                                                 | Sensibilidad                                                                                                                                                   |
| ------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Nombre técnico del addon**    | `pos_payrillium` (en rutas, XML ids, assets)                  | `pos_woodforest`                                       | Alta: si el folder ya es pos_woodforest, las rutas que digan pos_payrillium fallan (assets, refs).                                                             |
| **Modelos Odoo**                | `_name = 'payrillium.terminal'`                               | ¿`woodforest.terminal`?                                | **Muy alta**: la base de datos tiene tablas `payrillium_terminal`, `payrillium_config`, etc. Cambiar el \_name exige migración de tablas y de `ir.model.data`. |
| **Nombres de archivos**         | `payrillium_terminal_views.xml`, `payrillium_wizard.py`       | `woodforest_*.xml`, `woodforest_*.py`                  | Media: hay que actualizar referencias en manifest, imports, ref() en XML.                                                                                      |
| **Funciones / hooks**           | `show_payrillium_wizard_once`, `uninstall_cleanup_payrillium` | `show_woodforest_wizard_once`, etc.                    | Baja: solo nombres en Python y en manifest.                                                                                                                    |
| **Cadenas visibles al usuario** | "Payrillium Payment", "Payrillium Terminals"                  | "Woodforest Payment", "Woodforest Terminals"           | Baja.                                                                                                                                                          |
| **Comentarios / documentación** | Cualquier mención a Payrillium                                | Woodforest                                             | Baja.                                                                                                                                                          |
| **JS / assets**                 | `@pos_payrillium/js/...`, `pos_payrillium/static/...`         | `@pos_woodforest/...`, `pos_woodforest/static/...`     | Alta: debe coincidir con el nombre real del módulo.                                                                                                            |
| **XML ids**                     | `pos_payrillium.view_xxx`, `ref('pos_payrillium.xxx')`        | `pos_woodforest.view_xxx`, `ref('pos_woodforest.xxx')` | Alta: si el módulo se llama pos_woodforest, los ref deben ser pos_woodforest.                                                                                  |

---

## 2. Decisión crítica: nombres de modelos

En Odoo, el `_name` del modelo (ej. `payrillium.terminal`) define la tabla en la base de datos (`payrillium_terminal`). Si ya tienes instalado el módulo y hay datos:

- **Opción A – Mantener modelos como `payrillium.*`**

  - Cambias solo: nombre del addon en rutas/refs/assets, nombres de archivos, textos “Payrillium” → “Woodforest”, hooks, etc.
  - **No** cambias `_name` en los modelos.
  - Ventaja: no tocas la base de datos; actualizar el módulo es seguro.
  - Inconveniente: en código y en BD seguirá existiendo “payrillium” a nivel técnico.

- **Opción B – Cambiar modelos a `woodforest.*`**
  - Cambias también `_name` a `woodforest.terminal`, `woodforest.config`, etc.
  - Necesitas un **script de migración** que: renombre tablas (`payrillium_*` → `woodforest_*`), actualice `ir.model.data`, `ir.model.fields`, y otras referencias en BD.
  - Ventaja: todo el proyecto queda “woodforest” también a nivel técnico.
  - Inconveniente: más trabajo y riesgo si la migración no es exhaustiva.

Recomendación: si el módulo ya está en producción o con datos, usar **Opción A** primero (renombrar todo excepto `_name` de los modelos). Si el módulo es nuevo o puedes reinstalar/regenerar datos, se puede plantear la Opción B con migración.

---

## 3. Orden recomendado para hacer el cambio

1. **Backup** de la base de datos y del código.
2. **Manifest y datos del manifest**
   - `__manifest__.py`: nombre, summary, description, author, post_init_hook, uninstall_hook, rutas de assets (`pos_payrillium` → `pos_woodforest`), icon path.
3. **Rutas de assets**
   - Todas las entradas en `'assets'` que empiecen por `pos_payrillium/` → `pos_woodforest/`.
4. **Archivos XML**
   - En cada XML: `ref('pos_payrillium.xxx')` → `ref('pos_woodforest.xxx')`, y cualquier otro `pos_payrillium` en ids o atributos.
5. **Nombres de archivos**
   - Renombrar archivos que contengan “payrillium” en el nombre (p. ej. `payrillium_terminal_views.xml` → `woodforest_terminal_views.xml`) y actualizar referencias en manifest y en otros XML/Python.
6. **Python**
   - Imports entre módulos del addon si usan el nombre del addon.
   - Nombres de funciones/hooks (show*payrillium*_ → show*woodforest*_).
   - Cadenas visibles y comentarios “Payrillium” → “Woodforest”.
   - **No** cambiar `_name` de los modelos si eliges Opción A.
7. **JavaScript**
   - `@pos_payrillium/` → `@pos_woodforest/` en imports y en cualquier ruta que referencie el módulo.
8. **Seguridad y datos**
   - `ir.model.access.csv` y XML de seguridad: revisar que los `model_` sigan siendo los correctos (payrillium.terminal, etc. si mantienes Opción A).
   - Archivos de datos que referencien XML ids: actualizar a `pos_woodforest.*`.
9. **Pruebas**
   - Instalar/actualizar el módulo en una copia de la BD y comprobar: login, menús, formularios de terminales, POS, wizards, permisos.

---

## 4. Cómo se puede hacer (herramientas)

- **Búsqueda y reemplazo por tipo**

  - Por ejemplo: en todos los archivos del addon, reemplazar `pos_payrillium` por `pos_woodforest` (en manifest, XML, JS, Python, excepto donde no aplique).
  - Luego “Payrillium” → “Woodforest” en textos y comentarios.
  - Hacerlo por lotes (p. ej. primero manifest y assets, luego XML, luego Python, luego JS) y probar tras cada lote.

- **Renombrar archivos**

  - Listar archivos con “payrillium” en el nombre y renombrarlos a “woodforest”, actualizando todas las referencias (manifest, ref(), imports).

- **No usar reemplazo ciego** en:
  - Nombres de modelos (`payrillium.terminal` → solo si eliges Opción B y tienes migración).
  - Cadenas que sean identificadores externos (APIs, webhooks) a menos que también cambies el backend externo.

---

## 5. ¿Quién puede hacerlo?

- **Sí se puede hacer** de forma sistemática si:

  - Se sigue el plan por fases.
  - Se decide antes si se mantienen los nombres de modelos (Opción A) o se migran (Opción B).
  - Se hace backup y se prueba en entorno de desarrollo primero.

- **Recomendación**
  - Hacer primero solo el cambio de “marca” y rutas: addon `pos_woodforest`, assets, refs, textos “Woodforest”, hooks y nombres de archivos (Opción A).
  - Dejar el cambio de nombres de modelos (Opción B) para más adelante y con un script de migración específico si realmente lo necesitas.

---

## 6. Resumen

- **Objetivo:** que el proyecto pase de llamarse Payrillium a Woodforest en nombre del addon, rutas, textos, archivos y, opcionalmente, en modelos tras migración.
- **Mejor forma:** por fases (manifest → assets → XML → archivos → Python → JS), con decisión clara sobre si se mantienen o se cambian los `_name` de los modelos.
- **Cómo:** búsqueda/reemplazo por tipo + renombrado de archivos + actualización de referencias, con pruebas tras cada fase.
- **Sí es viable** hacerlo bien si se sigue este plan y se evita tocar la BD sin migración.

Si indicas si quieres **Opción A** (mantener modelos `payrillium.*`) o **Opción B** (cambiar a `woodforest.*` con migración), se puede bajar esto a una lista concreta de archivos y reemplazos paso a paso en tu repo.
