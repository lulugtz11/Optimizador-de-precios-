-- =====================================================================
-- Optimizador de menús · esquema de la base en la nube (Supabase / PostgreSQL)
--
-- Cómo usarlo: Supabase › SQL Editor › pegar todo este archivo › Run.
-- Se puede correr varias veces: borra y recrea las políticas y funciones.
--
-- Cómo cumple el aviso de privacidad:
--   1. Minimización: las tablas no tienen columnas para RFC, nombres, direcciones
--      ni fotos. La ubicación solo existe como alcaldía (lista cerrada).
--   2. Consentimiento: tabla `consentimientos` (historial, no se edita ni se borra
--      a mano). Usar el historial para mejorar recomendaciones requiere
--      `autoriza_mejora = true` en el registro más reciente.
--   3. Anonimización: cada fonda es un UUID con un perfil de categorías. El correo
--      vive solo en `auth.users` (lo administra Supabase); ninguna tabla de datos lo
--      copia. Al LLM le llegan el perfil, los platillos y los costos, nunca el correo.
--   4. Caducidad: `purgar_vencidos()` borra cada día las cuentas canceladas hace
--      más de 30 días; `fin_del_proyecto()` borra todos los datos de fondas.
--
-- Quién ve qué (Row Level Security):
--   * Cada fondero ve y escribe solo los datos de SU fonda.
--   * Ningún fondero puede ver datos de otro.
--   * El equipo lee todo SOLO desde el servidor, con la llave de servicio
--     (SUPABASE_SERVICE_KEY), para calcular KPIs y generar menús. Esa llave nunca
--     va en el tablero ni en git.
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------- catálogos cerrados ----------
do $$ begin
  create type public.tipo_fonda as enum ('comida_corrida', 'antojitos', 'desayunos');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.motivo_rechazo as enum ('muy_caro', 'no_le_gusta', 'repetitivo', 'no_se_preparar', 'otro');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.tiempo_platillo as enum ('entrada', 'plato_fuerte', 'guarnicion', 'bebida');
exception when duplicate_object then null; end $$;

-- ---------- tablas ----------
create table if not exists public.fondas (
  id                  uuid primary key default gen_random_uuid(),
  owner               uuid not null unique references auth.users (id) on delete cascade,
  tipo                public.tipo_fonda not null,
  comensales_dia      integer not null check (comensales_dia between 1 and 2000),
  alcaldia            text not null check (alcaldia in (
                        'Álvaro Obregón', 'Azcapotzalco', 'Benito Juárez', 'Coyoacán',
                        'Cuajimalpa de Morelos', 'Cuauhtémoc', 'Gustavo A. Madero', 'Iztacalco',
                        'Iztapalapa', 'La Magdalena Contreras', 'Miguel Hidalgo', 'Milpa Alta',
                        'Tláhuac', 'Tlalpan', 'Venustiano Carranza', 'Xochimilco')),
  presupuesto_semanal numeric(10, 2) not null check (presupuesto_semanal > 0),
  precio_comida       numeric(8, 2) not null default 90 check (precio_comida > 0),
  merma               numeric(4, 3) not null default 0.12 check (merma between 0 and 0.5),
  creada_en           timestamptz not null default now(),
  cancelada_en        timestamptz
);
comment on table public.fondas is 'Perfil anónimo de cada fonda: categorías, sin nombre ni dirección. owner es la única liga con la cuenta.';

create table if not exists public.consentimientos (
  id               bigserial primary key,
  fonda_id         uuid not null references public.fondas (id) on delete cascade,
  aviso_version    integer not null check (aviso_version > 0),
  autoriza_mejora  boolean not null,
  registrado_en    timestamptz not null default now()
);
comment on table public.consentimientos is 'Historial de aceptaciones del aviso y de la autorización opcional. Solo se agregan filas.';

create table if not exists public.platillos (
  id              uuid primary key default gen_random_uuid(),
  fonda_id        uuid not null references public.fondas (id) on delete cascade,
  clave_local     text not null,                      -- id que usa el tablero (propio_123...)
  nombre          text not null check (char_length(nombre) between 1 and 80),
  tiempo          public.tiempo_platillo not null,
  precio_venta    numeric(8, 2) check (precio_venta is null or precio_venta > 0),
  ingredientes    jsonb not null default '[]'::jsonb, -- [["jitomate", 120], ["cebolla", 30]]
  actualizado_en  timestamptz not null default now(),
  unique (fonda_id, clave_local)
);

create table if not exists public.respuestas_menu (
  id             bigserial primary key,
  fonda_id       uuid not null references public.fondas (id) on delete cascade,
  registrada_en  timestamptz not null default now(),
  horizonte_sem  integer not null check (horizonte_sem between 1 and 12),
  menu_titulo    text not null check (char_length(menu_titulo) <= 80),
  platillos      text[] not null default '{}',
  acepto         boolean not null,
  motivo         public.motivo_rechazo,
  check (acepto or motivo is not null)
);

create table if not exists public.gastos (
  id             bigserial primary key,
  fonda_id       uuid not null references public.fondas (id) on delete cascade,
  semana         date not null,
  plan           numeric(10, 2) not null check (plan >= 0),
  plan_min       numeric(10, 2) not null check (plan_min >= 0),
  plan_max       numeric(10, 2) not null check (plan_max >= 0),
  real           numeric(10, 2) not null check (real > 0),
  menu_titulo    text check (char_length(menu_titulo) <= 80),
  registrado_en  timestamptz not null default now()
);

-- Detalle de compras (minimización: solo insumo, cantidad, precio y fecha).
create table if not exists public.compras (
  id          bigserial primary key,
  fonda_id    uuid not null references public.fondas (id) on delete cascade,
  fecha       date not null,
  insumo_id   text not null check (char_length(insumo_id) <= 40),
  cantidad_kg numeric(10, 3) not null check (cantidad_kg > 0),
  precio      numeric(10, 2) not null check (precio >= 0)
);

-- Menús que el LLM propuso para cada fonda. Solo los escribe el servidor del equipo.
create table if not exists public.propuestas_menu (
  id             bigserial primary key,
  fonda_id       uuid not null references public.fondas (id) on delete cascade,
  generada_en    timestamptz not null default now(),
  horizonte_sem  integer not null,
  personalizada  boolean not null,  -- true si usó el historial de la fonda (con su autorización)
  menus          jsonb not null
);

create index if not exists respuestas_fonda_idx  on public.respuestas_menu (fonda_id, registrada_en desc);
create index if not exists gastos_fonda_idx      on public.gastos (fonda_id, semana desc);
create index if not exists consent_fonda_idx     on public.consentimientos (fonda_id, registrado_en desc);
create index if not exists propuestas_fonda_idx  on public.propuestas_menu (fonda_id, generada_en desc);

-- ---------- consentimiento vigente ----------
create or replace view public.consentimiento_actual
with (security_invoker = true) as
select distinct on (fonda_id) fonda_id, aviso_version, autoriza_mejora, registrado_en
from public.consentimientos
order by fonda_id, registrado_en desc, id desc;

-- ---------- ¿esta fonda es mía? ----------
create or replace function public.es_mi_fonda(f uuid)
returns boolean
language sql stable security definer
set search_path = public
as $$
  select exists (select 1 from public.fondas
                 where id = f and owner = auth.uid() and cancelada_en is null);
$$;
revoke all on function public.es_mi_fonda(uuid) from public;
grant execute on function public.es_mi_fonda(uuid) to authenticated;

-- ---------- Row Level Security ----------
alter table public.fondas           enable row level security;
alter table public.consentimientos  enable row level security;
alter table public.platillos        enable row level security;
alter table public.respuestas_menu  enable row level security;
alter table public.gastos           enable row level security;
alter table public.compras          enable row level security;
alter table public.propuestas_menu  enable row level security;

-- fondas: cada quien la suya; no se borra desde el tablero (se cancela).
drop policy if exists fondas_ver on public.fondas;
drop policy if exists fondas_crear on public.fondas;
drop policy if exists fondas_editar on public.fondas;
create policy fondas_ver    on public.fondas for select to authenticated using (owner = auth.uid() and cancelada_en is null);
create policy fondas_crear  on public.fondas for insert to authenticated with check (owner = auth.uid() and cancelada_en is null);
create policy fondas_editar on public.fondas for update to authenticated
  using (owner = auth.uid() and cancelada_en is null)
  with check (owner = auth.uid() and cancelada_en is null);

-- consentimientos: ver y agregar los propios; nunca editar ni borrar.
drop policy if exists consent_ver on public.consentimientos;
drop policy if exists consent_agregar on public.consentimientos;
create policy consent_ver     on public.consentimientos for select to authenticated using (public.es_mi_fonda(fonda_id));
create policy consent_agregar on public.consentimientos for insert to authenticated with check (public.es_mi_fonda(fonda_id));

-- platillos, respuestas, gastos y compras: el dueño hace todo con lo suyo.
do $$
declare t text;
begin
  foreach t in array array['platillos', 'respuestas_menu', 'gastos', 'compras'] loop
    execute format('drop policy if exists %1$s_dueno on public.%1$s', t);
    execute format('create policy %1$s_dueno on public.%1$s for all to authenticated
                    using (public.es_mi_fonda(fonda_id)) with check (public.es_mi_fonda(fonda_id))', t);
  end loop;
end $$;

-- propuestas: el dueño solo las lee; las escribe el servidor con la llave de servicio.
drop policy if exists propuestas_ver on public.propuestas_menu;
create policy propuestas_ver on public.propuestas_menu for select to authenticated using (public.es_mi_fonda(fonda_id));

-- ---------- permisos de tabla ----------
-- Los proyectos nuevos de Supabase ya no dan permisos automáticos sobre tablas nuevas.
-- Aquí se dan explícitamente; las políticas de arriba deciden QUÉ FILAS ve cada quien.
grant usage on schema public to authenticated, service_role;
grant select, insert, update                on public.fondas            to authenticated;
grant select, insert                        on public.consentimientos   to authenticated;
grant select                                on public.consentimiento_actual to authenticated;
grant select, insert, update, delete        on public.platillos, public.respuestas_menu,
                                               public.gastos, public.compras to authenticated;
grant select                                on public.propuestas_menu   to authenticated;
grant usage, select on all sequences in schema public to authenticated;
-- El servidor del equipo (llave secreta) lee todo y escribe propuestas.
grant all on public.fondas, public.consentimientos, public.platillos, public.respuestas_menu,
             public.gastos, public.compras, public.propuestas_menu to service_role;
grant select on public.consentimiento_actual to service_role;
grant usage, select on all sequences in schema public to service_role;

-- Nadie anónimo (sin cuenta) puede tocar nada.
revoke all on public.fondas, public.consentimientos, public.platillos, public.respuestas_menu,
              public.gastos, public.compras, public.propuestas_menu from anon;
revoke all on public.consentimiento_actual from anon;

-- ---------- cancelar la cuenta (desde el tablero) ----------
create or replace function public.cancelar_mi_cuenta()
returns void
language sql security definer
set search_path = public
as $$
  update public.fondas set cancelada_en = now()
  where owner = auth.uid() and cancelada_en is null;
$$;
revoke all on function public.cancelar_mi_cuenta() from public;
grant execute on function public.cancelar_mi_cuenta() to authenticated;

-- ---------- caducidad ----------
-- Borra las cuentas canceladas hace más de 30 días. Al borrar el usuario de auth,
-- el ON DELETE CASCADE borra su fonda y todos sus datos.
create or replace function public.purgar_vencidos()
returns integer
language plpgsql security definer
set search_path = public
as $$
declare n integer;
begin
  with vencidas as (
    delete from auth.users u
    using public.fondas f
    where f.owner = u.id and f.cancelada_en < now() - interval '30 days'
    returning u.id)
  select count(*) into n from vencidas;
  return n;
end $$;
revoke all on function public.purgar_vencidos() from public, anon, authenticated;

-- Al terminar el proyecto: borra todas las cuentas que tienen fonda y sus datos.
create or replace function public.fin_del_proyecto()
returns integer
language plpgsql security definer
set search_path = public
as $$
declare n integer;
begin
  with todas as (
    delete from auth.users u using public.fondas f where f.owner = u.id returning u.id)
  select count(*) into n from todas;
  return n;
end $$;
revoke all on function public.fin_del_proyecto() from public, anon, authenticated;

-- ---------- vistas para el equipo (sin owner ni correo) ----------
create schema if not exists equipo;
revoke all on schema equipo from public, anon, authenticated;

create or replace view equipo.kpi2_gasto as
select g.fonda_id, f.tipo, g.semana, g.plan, g.plan_min, g.plan_max, g.real,
       round(100 * abs(g.real - g.plan) / g.real, 2) as error_pct,
       (g.real between g.plan_min and g.plan_max) as dentro_del_rango
from public.gastos g join public.fondas f on f.id = g.fonda_id;

create or replace view equipo.kpi3_aceptacion as
select r.fonda_id, f.tipo, r.registrada_en, r.acepto, r.motivo, r.horizonte_sem
from public.respuestas_menu r join public.fondas f on f.id = r.fonda_id;

grant usage on schema equipo to service_role;
grant select on all tables in schema equipo to service_role;

-- ---------- tarea diaria de borrado (03:15, hora UTC) ----------
-- Requiere la extensión pg_cron (Supabase › Database › Extensions › pg_cron).
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.unschedule(jobid) from cron.job where jobname = 'purgar_vencidos';
    perform cron.schedule('purgar_vencidos', '15 3 * * *', 'select public.purgar_vencidos()');
  else
    raise notice 'pg_cron no está activo: activa la extensión y vuelve a correr este archivo para programar el borrado diario.';
  end if;
end $$;
