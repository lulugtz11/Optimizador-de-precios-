# Cuentas en la nube: cómo activarlas

Sin esta configuración el tablero sigue funcionando igual que antes: todo se guarda solo en el
dispositivo del fondero. Con ella, cada fondero puede crear una cuenta y sus datos se guardan en
una base segura en Supabase (plan gratuito).

## Qué resuelve

| Necesidad | Cómo se cumple |
|---|---|
| Cada fondero ve solo lo suyo | Permisos por fila (Row Level Security) en la base: aunque alguien modifique el tablero, la base rechaza leer o escribir datos de otra fonda. Probado en `esquema.sql`. |
| El equipo mide los KPIs | `python -m insumos kpis_nube` lee todo con la llave de servicio y genera `reports/kpis_nube.md`, solo con cifras agregadas (grupos de 3 fondas o más). |
| El LLM mejora solo para cada fonda | `python -m insumos menus_nube` arma un prompt por fonda. Si esa fonda dio la autorización opcional, incluye **su** historial de menús aceptados y rechazados; nunca el de otra. No se entrena ningún modelo. |
| Minimización | Las tablas no tienen columnas para RFC, nombre, dirección ni fotos. La ubicación es una lista cerrada de alcaldías. |
| Consentimiento | Aviso obligatorio al entrar. La autorización para personalizar es aparte, opcional y se puede quitar; cada cambio queda registrado con fecha en `consentimientos`. |
| Anonimización | Cada fonda es un identificador con un perfil de categorías. El correo solo existe en el sistema de cuentas de Supabase, nunca junto a los datos de la fonda. Al LLM no le llega ni el identificador. |
| Caducidad | "Cancelar mi cuenta" la desactiva al momento; una tarea diaria borra todo 30 días después. `fin_del_proyecto()` borra todas las fondas al cerrar el proyecto. Cada vez que corre `menus_nube` se borran las bitácoras de prompts de cuentas canceladas y las que tenían el historial de fondas que retiraron su autorización. |

## Pasos (una sola vez, ~15 minutos)

1. Crea un proyecto gratis en <https://supabase.com> (región más cercana: `us-east` o `us-west`).
2. En **Database › Extensions**, activa `pg_cron` (para el borrado automático a los 30 días).
3. En **SQL Editor**, pega todo `supabase/esquema.sql` y dale **Run**.
4. En **Authentication › Providers › Email**, deja activado "Confirm email" (el fondero confirma su
   correo antes de entrar).
5. En **Project Settings › API Keys** copia dos llaves y en **Project Settings › Data API** la URL:
   ```
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_ANON_KEY=sb_publishable_...    # Publishable key: va dentro del tablero, es pública por diseño
   SUPABASE_SERVICE_KEY=sb_secret_...      # Secret key: SOLO en el .env del equipo
   ```
   (Si tu proyecto muestra las llaves antiguas "anon" y "service_role", también funcionan.)
6. Regenera el tablero: `python -m insumos dashboard`. Aparece "Mi cuenta" al pie de la página.

## Uso semanal del equipo

```powershell
python -m insumos menus_nube    # menús para cada fonda con cuenta (necesita ANTHROPIC_API_KEY)
python -m insumos kpis_nube     # KPIs con fondas reales -> reports/kpis_nube.md
```

## Reglas de seguridad

- **La llave de servicio nunca sale del `.env` del equipo.** Ve los datos de todas las fondas. No
  va en el tablero, en el código, en capturas de pantalla ni en git (`.env*` ya está en `.gitignore`).
- La llave `anon` sí puede estar en el tablero: sin una cuenta no puede leer nada, y con cuenta solo
  ve lo de esa fonda.
- Para borrar todo al terminar el proyecto: en el SQL Editor, `select public.fin_del_proyecto();`

## Limitaciones conocidas

- El tablero necesita internet para la cuenta. Sin conexión, sigue guardando en el dispositivo y no
  sincroniza lo capturado sin conexión (se puede agregar después).
- No hay recuperación de contraseña desde el tablero; se hace desde Supabase.
- Los datos capturados antes de crear la cuenta (en "Mi fonda nueva") no se suben solos.
