CREATE TABLE IF NOT EXISTS public.property_corpus (
    record_id text PRIMARY KEY,
    publisher text NOT NULL,
    record_type text NOT NULL CHECK (record_type IN ('project', 'buyer_guide')),
    title text NOT NULL,
    searchable_text text NOT NULL,
    source_url text NOT NULL,
    canonical_url text NOT NULL,
    collected_at timestamptz NOT NULL,
    source_as_of date,
    collection_method text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    supporting_sources jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(supporting_sources) = 'array')
);

ALTER TABLE public.property_corpus ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.property_corpus FROM PUBLIC, anon, authenticated, service_role;
