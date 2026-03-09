--
-- PostgreSQL database dump
--

\restrict t5fWjewYcNipMQ1sADnfErYIiXEbuS54YKWFImccMGLDCBDxbigGaUEG0ymZ81N

-- Dumped from database version 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO postgres;

--
-- Name: alert_subscriptions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alert_subscriptions (
    id integer NOT NULL,
    stock_code character varying(16) NOT NULL,
    subscriber character varying(64) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone NOT NULL
);


ALTER TABLE public.alert_subscriptions OWNER TO postgres;

--
-- Name: alert_subscriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.alert_subscriptions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.alert_subscriptions_id_seq OWNER TO postgres;

--
-- Name: alert_subscriptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.alert_subscriptions_id_seq OWNED BY public.alert_subscriptions.id;


--
-- Name: daily_performance; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.daily_performance (
    id integer NOT NULL,
    recommendation_id integer NOT NULL,
    date date NOT NULL,
    close_price double precision NOT NULL,
    high_price double precision NOT NULL,
    low_price double precision NOT NULL,
    pnl_percent double precision NOT NULL,
    max_drawdown double precision NOT NULL,
    evaluation_score double precision NOT NULL,
    sharpe_ratio double precision DEFAULT '0'::double precision NOT NULL,
    logic_validated boolean DEFAULT false NOT NULL,
    market_cap_score double precision DEFAULT '50'::double precision NOT NULL,
    elasticity_score double precision DEFAULT '50'::double precision NOT NULL,
    liquidity_score double precision DEFAULT '50'::double precision NOT NULL,
    notes text DEFAULT ''::text NOT NULL
);


ALTER TABLE public.daily_performance OWNER TO postgres;

--
-- Name: daily_performance_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.daily_performance_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.daily_performance_id_seq OWNER TO postgres;

--
-- Name: daily_performance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.daily_performance_id_seq OWNED BY public.daily_performance.id;


--
-- Name: news_discovery_candidates; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.news_discovery_candidates (
    id integer NOT NULL,
    stock_code character varying(16) NOT NULL,
    stock_name character varying(64) DEFAULT ''::character varying NOT NULL,
    headline character varying(255) NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    source_site character varying(255) DEFAULT ''::character varying NOT NULL,
    source_url character varying(500) DEFAULT ''::character varying NOT NULL,
    event_type character varying(64) DEFAULT 'generic'::character varying NOT NULL,
    discovery_score double precision DEFAULT '0'::double precision NOT NULL,
    status character varying(32) DEFAULT 'candidate'::character varying NOT NULL,
    discovered_at timestamp without time zone NOT NULL,
    last_seen_at timestamp without time zone NOT NULL,
    promoted_recommendation_id integer
);


ALTER TABLE public.news_discovery_candidates OWNER TO postgres;

--
-- Name: news_discovery_candidates_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.news_discovery_candidates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.news_discovery_candidates_id_seq OWNER TO postgres;

--
-- Name: news_discovery_candidates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.news_discovery_candidates_id_seq OWNED BY public.news_discovery_candidates.id;


--
-- Name: recommendations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.recommendations (
    id integer NOT NULL,
    stock_id integer NOT NULL,
    recommender_id integer NOT NULL,
    recommend_ts timestamp without time zone NOT NULL,
    initial_price double precision,
    original_message text NOT NULL,
    extracted_logic text DEFAULT ''::text NOT NULL,
    status character varying(32) DEFAULT 'tracking'::character varying NOT NULL,
    source character varying(32) DEFAULT 'wechat'::character varying NOT NULL
);


ALTER TABLE public.recommendations OWNER TO postgres;

--
-- Name: recommendations_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.recommendations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.recommendations_id_seq OWNER TO postgres;

--
-- Name: recommendations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.recommendations_id_seq OWNED BY public.recommendations.id;


--
-- Name: recommenders; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.recommenders (
    id integer NOT NULL,
    name character varying(64) NOT NULL,
    wechat_id character varying(64) DEFAULT ''::character varying NOT NULL,
    reliability_score double precision DEFAULT '50'::double precision NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


ALTER TABLE public.recommenders OWNER TO postgres;

--
-- Name: recommenders_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.recommenders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.recommenders_id_seq OWNER TO postgres;

--
-- Name: recommenders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.recommenders_id_seq OWNED BY public.recommenders.id;


--
-- Name: stock_predictions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.stock_predictions (
    id integer NOT NULL,
    stock_code character varying(16) NOT NULL,
    stock_name character varying(64) DEFAULT ''::character varying NOT NULL,
    prediction_date date NOT NULL,
    horizon_days integer DEFAULT 1 NOT NULL,
    direction character varying(16) DEFAULT 'sideways'::character varying NOT NULL,
    confidence double precision DEFAULT '0'::double precision NOT NULL,
    thesis text DEFAULT ''::text NOT NULL,
    invalidation_conditions text DEFAULT ''::text NOT NULL,
    risk_flags text DEFAULT '[]'::text NOT NULL,
    evidence text DEFAULT '[]'::text NOT NULL,
    predicted_by character varying(64) DEFAULT 'llm'::character varying NOT NULL,
    actual_pnl_percent double precision,
    review_result character varying(32) DEFAULT 'pending'::character varying NOT NULL,
    review_notes text DEFAULT ''::text NOT NULL,
    reviewed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


ALTER TABLE public.stock_predictions OWNER TO postgres;

--
-- Name: stock_predictions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.stock_predictions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.stock_predictions_id_seq OWNER TO postgres;

--
-- Name: stock_predictions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.stock_predictions_id_seq OWNED BY public.stock_predictions.id;


--
-- Name: stocks; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.stocks (
    id integer NOT NULL,
    stock_code character varying(16) NOT NULL,
    stock_name character varying(64) DEFAULT ''::character varying NOT NULL,
    industry character varying(64) DEFAULT ''::character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


ALTER TABLE public.stocks OWNER TO postgres;

--
-- Name: stocks_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.stocks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.stocks_id_seq OWNER TO postgres;

--
-- Name: stocks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.stocks_id_seq OWNED BY public.stocks.id;


--
-- Name: alert_subscriptions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alert_subscriptions ALTER COLUMN id SET DEFAULT nextval('public.alert_subscriptions_id_seq'::regclass);


--
-- Name: daily_performance id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.daily_performance ALTER COLUMN id SET DEFAULT nextval('public.daily_performance_id_seq'::regclass);


--
-- Name: news_discovery_candidates id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.news_discovery_candidates ALTER COLUMN id SET DEFAULT nextval('public.news_discovery_candidates_id_seq'::regclass);


--
-- Name: recommendations id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recommendations ALTER COLUMN id SET DEFAULT nextval('public.recommendations_id_seq'::regclass);


--
-- Name: recommenders id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recommenders ALTER COLUMN id SET DEFAULT nextval('public.recommenders_id_seq'::regclass);


--
-- Name: stock_predictions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.stock_predictions ALTER COLUMN id SET DEFAULT nextval('public.stock_predictions_id_seq'::regclass);


--
-- Name: stocks id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.stocks ALTER COLUMN id SET DEFAULT nextval('public.stocks_id_seq'::regclass);


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.alembic_version (version_num) FROM stdin;
20260303_0003
\.


--
-- Data for Name: alert_subscriptions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.alert_subscriptions (id, stock_code, subscriber, is_active, created_at) FROM stdin;
\.


--
-- Data for Name: daily_performance; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.daily_performance (id, recommendation_id, date, close_price, high_price, low_price, pnl_percent, max_drawdown, evaluation_score, sharpe_ratio, logic_validated, market_cap_score, elasticity_score, liquidity_score, notes) FROM stdin;
\.


--
-- Data for Name: news_discovery_candidates; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.news_discovery_candidates (id, stock_code, stock_name, headline, summary, source_site, source_url, event_type, discovery_score, status, discovered_at, last_seen_at, promoted_recommendation_id) FROM stdin;
\.


--
-- Data for Name: recommendations; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.recommendations (id, stock_id, recommender_id, recommend_ts, initial_price, original_message, extracted_logic, status, source) FROM stdin;
1	1	1	2026-03-09 13:56:09.075245	\N	002436 看好，逻辑是订单增长与景气回暖	002436 看好，逻辑是订单增长与景气回暖	tracking	openclaw_qq
2	2	1	2026-03-09 15:00:27.560286	\N	002384 看好，逻辑是消费电子复苏	002384 看好，逻辑是消费电子复苏	tracking	openclaw_qq
\.


--
-- Data for Name: recommenders; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.recommenders (id, name, wechat_id, reliability_score, notes, created_at, updated_at) FROM stdin;
1	QQ群友A	qq_user_1	50		2026-03-09 05:56:09.082179	2026-03-09 05:56:09.082182
\.


--
-- Data for Name: stock_predictions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.stock_predictions (id, stock_code, stock_name, prediction_date, horizon_days, direction, confidence, thesis, invalidation_conditions, risk_flags, evidence, predicted_by, actual_pnl_percent, review_result, review_notes, reviewed_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: stocks; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.stocks (id, stock_code, stock_name, industry, created_at, updated_at) FROM stdin;
1	002436			2026-03-09 05:56:09.078948	2026-03-09 05:56:09.078952
2	002384			2026-03-09 07:00:27.593383	2026-03-09 07:00:27.59339
\.


--
-- Name: alert_subscriptions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.alert_subscriptions_id_seq', 1, false);


--
-- Name: daily_performance_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.daily_performance_id_seq', 1, false);


--
-- Name: news_discovery_candidates_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.news_discovery_candidates_id_seq', 1, false);


--
-- Name: recommendations_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.recommendations_id_seq', 2, true);


--
-- Name: recommenders_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.recommenders_id_seq', 1, true);


--
-- Name: stock_predictions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.stock_predictions_id_seq', 1, false);


--
-- Name: stocks_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.stocks_id_seq', 2, true);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: alert_subscriptions alert_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alert_subscriptions
    ADD CONSTRAINT alert_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: daily_performance daily_performance_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.daily_performance
    ADD CONSTRAINT daily_performance_pkey PRIMARY KEY (id);


--
-- Name: news_discovery_candidates news_discovery_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.news_discovery_candidates
    ADD CONSTRAINT news_discovery_candidates_pkey PRIMARY KEY (id);


--
-- Name: recommendations recommendations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recommendations
    ADD CONSTRAINT recommendations_pkey PRIMARY KEY (id);


--
-- Name: recommenders recommenders_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recommenders
    ADD CONSTRAINT recommenders_pkey PRIMARY KEY (id);


--
-- Name: stock_predictions stock_predictions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.stock_predictions
    ADD CONSTRAINT stock_predictions_pkey PRIMARY KEY (id);


--
-- Name: stocks stocks_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.stocks
    ADD CONSTRAINT stocks_pkey PRIMARY KEY (id);


--
-- Name: alert_subscriptions uq_alert_subscriptions_stock_subscriber; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alert_subscriptions
    ADD CONSTRAINT uq_alert_subscriptions_stock_subscriber UNIQUE (stock_code, subscriber);


--
-- Name: daily_performance uq_daily_performance_reco_date; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.daily_performance
    ADD CONSTRAINT uq_daily_performance_reco_date UNIQUE (recommendation_id, date);


--
-- Name: news_discovery_candidates uq_news_candidate_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.news_discovery_candidates
    ADD CONSTRAINT uq_news_candidate_unique UNIQUE (stock_code, headline, source_url);


--
-- Name: stock_predictions uq_stock_prediction_date; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.stock_predictions
    ADD CONSTRAINT uq_stock_prediction_date UNIQUE (stock_code, prediction_date);


--
-- Name: stocks uq_stocks_stock_code; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.stocks
    ADD CONSTRAINT uq_stocks_stock_code UNIQUE (stock_code);


--
-- Name: ix_alert_subscriptions_stock_code; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_alert_subscriptions_stock_code ON public.alert_subscriptions USING btree (stock_code);


--
-- Name: ix_alert_subscriptions_subscriber; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_alert_subscriptions_subscriber ON public.alert_subscriptions USING btree (subscriber);


--
-- Name: ix_daily_performance_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_daily_performance_date ON public.daily_performance USING btree (date);


--
-- Name: ix_daily_performance_recommendation_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_daily_performance_recommendation_id ON public.daily_performance USING btree (recommendation_id);


--
-- Name: ix_news_discovery_candidates_discovered_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_news_discovery_candidates_discovered_at ON public.news_discovery_candidates USING btree (discovered_at);


--
-- Name: ix_news_discovery_candidates_last_seen_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_news_discovery_candidates_last_seen_at ON public.news_discovery_candidates USING btree (last_seen_at);


--
-- Name: ix_news_discovery_candidates_stock_code; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_news_discovery_candidates_stock_code ON public.news_discovery_candidates USING btree (stock_code);


--
-- Name: ix_recommendations_recommend_ts; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_recommendations_recommend_ts ON public.recommendations USING btree (recommend_ts);


--
-- Name: ix_recommendations_recommender_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_recommendations_recommender_id ON public.recommendations USING btree (recommender_id);


--
-- Name: ix_recommendations_stock_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_recommendations_stock_id ON public.recommendations USING btree (stock_id);


--
-- Name: ix_recommenders_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_recommenders_name ON public.recommenders USING btree (name);


--
-- Name: ix_stock_predictions_prediction_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_stock_predictions_prediction_date ON public.stock_predictions USING btree (prediction_date);


--
-- Name: ix_stock_predictions_stock_code; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_stock_predictions_stock_code ON public.stock_predictions USING btree (stock_code);


--
-- Name: ix_stocks_stock_code; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_stocks_stock_code ON public.stocks USING btree (stock_code);


--
-- Name: daily_performance daily_performance_recommendation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.daily_performance
    ADD CONSTRAINT daily_performance_recommendation_id_fkey FOREIGN KEY (recommendation_id) REFERENCES public.recommendations(id) ON DELETE CASCADE;


--
-- Name: news_discovery_candidates news_discovery_candidates_promoted_recommendation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.news_discovery_candidates
    ADD CONSTRAINT news_discovery_candidates_promoted_recommendation_id_fkey FOREIGN KEY (promoted_recommendation_id) REFERENCES public.recommendations(id) ON DELETE SET NULL;


--
-- Name: recommendations recommendations_recommender_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recommendations
    ADD CONSTRAINT recommendations_recommender_id_fkey FOREIGN KEY (recommender_id) REFERENCES public.recommenders(id) ON DELETE CASCADE;


--
-- Name: recommendations recommendations_stock_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.recommendations
    ADD CONSTRAINT recommendations_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict t5fWjewYcNipMQ1sADnfErYIiXEbuS54YKWFImccMGLDCBDxbigGaUEG0ymZ81N

