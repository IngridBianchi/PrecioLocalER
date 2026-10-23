-- ============================================================
-- PrecioLocal ER — 002_seed.sql
-- Cadenas reales de Entre Ríos + jerarquía de categorías (2 niveles)
-- + marcas de góndola. Idempotente: slugs únicos como llave natural
-- (ON CONFLICT DO NOTHING). Consolidación de los antiguos
-- 002_seed_catalog.sql y 002_seed_chains_categories.sql.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- Cadenas (slugs = contrato en docs/contracts.md; scrape_schedule
-- es un cron de apscheduler reservado para Fases posteriores)
-- ------------------------------------------------------------
INSERT INTO chains (name, slug, domain, region, active, scrape_schedule)
VALUES
    ('La Anónima',          'la_anonima',          'laanonima.com.ar',       'entre_rios', true, '0 7 * * 1'),
    ('Cooperativa Obrera',  'cooperativa_obrera',  'cooperativaobrera.coop', 'entre_rios', true, '0 8 * * 1'),
    ('ChangoMás',           'changomas',           'changomas.com.ar',       'entre_rios', true, '0 9 * * 1')
ON CONFLICT (slug) DO NOTHING;

-- ------------------------------------------------------------
-- Categorías (jerárquico de 2 niveles) — raíces primero
-- ------------------------------------------------------------
INSERT INTO categories (name, slug) VALUES
    ('Almacén',             'almacen'),
    ('Bebidas',             'bebidas'),
    ('Lácteos',             'lacteos'),
    ('Limpieza',            'limpieza'),
    ('Cuidado personal',    'cuidado-personal'),
    ('Bebés y cuidado',     'bebes-y-cuidado')
ON CONFLICT (slug) DO NOTHING;

-- Hijas (resuelven el id del padre por slug)
INSERT INTO categories (name, slug, parent_id)
SELECT c.name, c.slug, p.id
FROM (VALUES
    -- almacen
    ('Aceites',              'aceites',              'almacen'),
    ('Harinas',              'harinas',              'almacen'),
    ('Arroces y pastas',     'arroces-y-pastas',     'almacen'),
    ('Azúcar',               'azucar',               'almacen'),
    ('Conservas',            'conservas',            'almacen'),
    ('Dulces',               'dulces',               'almacen'),
    ('Galletitas y copetín', 'galletitas-y-copetin', 'almacen'),
    -- bebidas
    ('Agua',                 'agua',                 'bebidas'),
    ('Gaseosas',             'gaseosas',             'bebidas'),
    ('Jugos',                'jugos',                'bebidas'),
    ('Cervezas',             'cervezas',             'bebidas'),
    ('Vinos',                'vinos',                'bebidas'),
    -- lacteos
    ('Leche y yogur',        'leche-y-yogur',        'lacteos'),
    ('Quesos',               'quesos',               'lacteos'),
    ('Manteca y crema',      'manteca-y-crema',      'lacteos'),
    -- limpieza
    ('Detergentes',          'detergentes',          'limpieza'),
    ('Lavandina',            'lavandina',            'limpieza'),
    -- cuidado personal
    ('Papel higiénico',      'papel-higienico',      'cuidado-personal'),
    ('Higiene dental',       'higiene-dental',       'cuidado-personal'),
    ('Shampoo y acondicionador', 'shampoo-y-acondicionador', 'cuidado-personal'),
    -- bebes y cuidado
    ('Pañales y toallitas',  'panales-y-toallitas',  'bebes-y-cuidado')
) AS c(name, slug, parent_slug)
JOIN categories p ON p.slug = c.parent_slug
ON CONFLICT (slug) DO NOTHING;

-- ------------------------------------------------------------
-- Marcas (ejemplos reales de góndola ER)
-- ------------------------------------------------------------
INSERT INTO brands (name, slug) VALUES
    ('Natura',                    'natura'),
    ('Cocinero',                  'cocinero'),
    ('Molinos Río de la Plata',   'molinos-rio-de-la-plata'),
    ('Sancor',                    'sancor'),
    ('La Serenísima',             'la-serenisima'),
    ('Aguas Danone / Villavicencio', 'villavicencio'),
    ('Manfrey',                   'manfrey'),
    ('Quilmes',                   'quilmes'),
    ('La Anónima (marca propia)', 'la-anonima-propia'),
    ('Gallinero',                 'gallinero'),
    ('La Sibila',                 'la-sibila'),
    ('Tregar',                    'tregar'),
    ('Cif',                       'cif'),
    ('Colgate',                   'colgate'),
    ('Procter & Gamble',          'pandg')
ON CONFLICT (slug) DO NOTHING;

COMMIT;