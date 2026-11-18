CREATE TABLE IF NOT EXISTS public.property_listing_release (
    version text PRIMARY KEY,
    digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS public.property_listing (
    release text NOT NULL REFERENCES public.property_listing_release(version),
    listing_id text NOT NULL,
    source text NOT NULL CHECK (source IN ('darglobal', 'wasalt')),
    source_id text NOT NULL,
    source_url text NOT NULL,
    captured_at timestamptz NOT NULL,
    city text,
    city_key text,
    district text,
    property_type text,
    transaction_type text CHECK (transaction_type IN ('sale', 'rent')),
    asking_price numeric CHECK (asking_price > 0),
    currency text CHECK (currency ~ '^[A-Z]{3}$'),
    rent_period text CHECK (
        rent_period IS NULL OR (rent_period IN ('month', 'year') AND transaction_type IS NOT DISTINCT FROM 'rent')
    ),
    bedrooms integer CHECK (bedrooms >= 0),
    area_sqm numeric CHECK (area_sqm > 0),
    features text[] NOT NULL,
    unit_key text,
    PRIMARY KEY (release, listing_id),
    UNIQUE (release, unit_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS property_listing_wasalt_url
    ON public.property_listing (release, source_url) WHERE source = 'wasalt';
CREATE INDEX IF NOT EXISTS property_listing_filters
    ON public.property_listing (release, city_key, bedrooms, currency, asking_price);

ALTER TABLE public.property_listing_release ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.property_listing ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.property_listing_release, public.property_listing
    FROM PUBLIC, anon, authenticated, service_role;
