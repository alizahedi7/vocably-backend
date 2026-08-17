"""Application configuration, loaded from the environment via pydantic-settings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, ClassVar, Literal

from pydantic import Field, PostgresDsn, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]

#: The gateways that speak the OpenAI Chat Completions protocol and can therefore
#: appear in a failover chain. Named here rather than imported from
#: ``app.infrastructure.ai.providers`` because ``core`` must not depend on
#: ``infrastructure`` — the adapter package imports ``core.logging``, and reaching
#: back the other way is a circular import. ``test_providers_match_settings``
#: pins the two lists together, so adding a class without its settings block (or
#: the reverse) fails a test rather than a deploy.
OPENAI_PROTOCOL_PROVIDERS: tuple[str, ...] = ("avalai", "gapgpt", "openrouter")


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """One OpenAI-protocol gateway's settings, resolved and validated.

    Exists so the factory builds a gateway from one object instead of six
    ``getattr`` calls, and so "is this gateway usable?" is answered once, at
    boot, by whatever asks for the profile — rather than per-request, by an
    exception in the middle of a learner's lookup.
    """

    name: str
    api_key: str
    model: str
    base_url: str = ""
    timeout_seconds: float = 30.0
    max_tokens: int = 4096
    extra_headers: dict[str, str] = field(default_factory=dict)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # ENV_FILE lets the test suite point this at os.devnull so a developer's
        # local .env (e.g. real Google/Kavenegar credentials for manual testing)
        # never leaks into test runs. See tests/conftest.py.
        env_file=os.environ.get("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    #: 32 bytes is HMAC-SHA256's block size; below it the key, not the algorithm,
    #: is the weak point. ClassVar so pydantic-settings reads it as a constant
    #: rather than another thing the environment may override.
    MIN_SECRET_KEY_LENGTH: ClassVar[int] = 32

    # ── Application ───────────────────────────────────────────
    project_name: str = "Vocably"
    environment: Environment = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    #: Swagger UI, ReDoc and openapi.json. Off in production by default, on
    #: everywhere else. The spec is a complete machine-readable map of every
    #: route, parameter and enum — the input format automated scanners want —
    #: and no first-party client reads it at runtime: the Flutter app, the PWA
    #: and vocably-admin are all built against a spec, never a live one. Swagger
    #: UI additionally puts a "Try it out" console against real user data on the
    #: production origin, and CDN-loads its own bundle there.
    #:
    #: Not a secret and not a security boundary — authorization is per-route
    #: (`CurrentAdmin`, `DeckAccess`) and stays the thing that matters. This only
    #: declines to hand out the map. Set EXPOSE_DOCS=true to override
    #: deliberately, which is what a staging box should do.
    expose_docs: bool | None = None
    # NoDecode: skip pydantic-settings' JSON pre-parsing so the validator below can
    # accept a plain comma-separated string from the environment.
    backend_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ── Security / JWT ────────────────────────────────────────
    secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # ── Database ──────────────────────────────────────────────
    database_url: str | None = None
    postgres_user: str = "vocably"
    postgres_password: str = "vocably"
    postgres_db: str = "vocably"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # ── Auth adapters ─────────────────────────────────────────
    otp_length: int = 6
    otp_ttl_seconds: int = 300
    otp_resend_cooldown_seconds: int = 30
    # Cost-abuse backstop: SMS requests allowed per client IP per hour (<= 0 disables).
    otp_requests_per_ip_per_hour: int = 20

    #: Shared store for the limits that are *security* controls rather than
    #: cost backstops — handle enumeration and invite-code guessing. The
    #: in-process limiter multiplies its budget by the worker count and resets
    #: on restart, which is fine for SMS spend and wrong for these two.
    #: Unreachable Redis degrades to the in-process limiter; it never fails open.
    rate_limit_redis_url: str = "redis://localhost:6379/2"
    #: Handle-availability checks per user per hour. The endpoint is typed into
    #: as the learner types, so this is generous — it exists to stop the
    #: endpoint being walked as a handle-enumeration oracle, not to stop typing.
    username_checks_per_user_per_hour: int = 120
    #: People-searches per user per hour. Tighter than the availability check
    #: because one call here returns up to eight handles rather than a yes/no,
    #: so walking the namespace with it is that many times cheaper — and it is
    #: called on a debounce rather than per keystroke, so a person sharing a
    #: deck with a class never comes near this.
    user_searches_per_user_per_hour: int = 60
    #: Join attempts per user and per IP per hour. This is the one endpoint
    #: where guessing wins access to someone else's data.
    joins_per_user_per_hour: int = 20
    #: Deliberately loose: a class joins from one school's WiFi, so this is a
    #: shared budget for everyone behind that NAT. It is a brute-force ceiling,
    #: not a fairness quota — the code's own entropy is what makes guessing
    #: hopeless, and a teacher's second class must not be locked out by the
    #: first one's joins.
    joins_per_ip_per_hour: int = 600
    #: Shares and friend-adds per user per hour. Both resolve a handle to a
    #: person, so without a cap they are a slower handle-enumeration oracle
    #: than the availability endpoint.
    shares_per_user_per_hour: int = 60
    #: Written feedback reports per user per hour. The one endpoint that stores
    #: unbounded free text on request, so the cap is about storage and moderation
    #: load rather than enumeration. Ten is far past anything an honest reporter
    #: does in an hour and well short of a nuisance.
    feedback_reports_per_user_per_hour: int = 10
    #: AI card ratings per user per hour. Deliberately loose: rating is an upsert
    #: keyed by (user, lookup, sense), so a session adding thirty cards writes at
    #: most a few dozen rows however many times the thumbs are tapped. This is a
    #: ceiling on a stuck client, not a budget the learner can feel.
    ai_feedback_per_user_per_hour: int = 300
    # DEV/TEST ONLY: issue this exact code instead of a random one, for every phone
    # number, so mobile/QA can sign in without reading server logs. Forbidden in
    # production (validated below) — it disables OTP secrecy entirely while set.
    otp_fixed_code: str = ""
    otp_sender: Literal["console", "kavenegar", "sms_ir"] = "console"
    kavenegar_api_key: str = ""
    kavenegar_otp_template: str = ""
    sms_ir_api_key: str = ""
    sms_ir_template_id: int = 0
    google_verifier: Literal["stub", "google"] = "stub"
    google_client_id: str = ""
    #: Hard ceiling on the Google tokeninfo call. Sits in the login path, so a
    #: slow response should fail fast rather than hang the request.
    google_tokeninfo_timeout_seconds: float = 5.0

    # ── AI services ───────────────────────────────────────────
    #: The gateway tried first. ``stub`` is offline canned data and what the
    #: tests run against.
    ai_provider: Literal["stub", "anthropic", "avalai", "gapgpt", "openrouter"] = "stub"
    #: Ordered, comma-separated gateways to try when ``AI_PROVIDER`` fails, e.g.
    #: ``AI_FALLBACK_PROVIDERS=gapgpt,openrouter``. Empty means no failover, and
    #: is the default because a fallback naming an unfunded account buys nothing
    #: but latency. Only OpenAI-protocol gateways can appear here — the Anthropic
    #: adapter speaks a different wire format and ``stub`` would mask a real
    #: outage behind canned data.
    ai_fallback_providers: str = ""
    #: Ceiling on one lookup across *every* gateway tried. Three gateways at a
    #: 30 s timeout would otherwise stack into 90 s of spinner, which is worse
    #: for the learner than the single-gateway failure failover exists to fix.
    ai_failover_deadline_seconds: float = 45.0
    #: Consecutive failures before a gateway is skipped, and for how long. See
    #: ``failover_ai_service`` — the breaker is an optimisation, never a
    #: correctness mechanism.
    ai_breaker_threshold: int = Field(3, ge=1, le=100)
    ai_breaker_cooldown_seconds: float = Field(60.0, ge=1.0, le=3600.0)
    #: Required when AI_PROVIDER=anthropic. Never commit a value — set it in the
    #: environment. Rotate immediately if it leaks.
    anthropic_api_key: str = ""
    #: Override to route through a gateway/proxy that speaks the Anthropic API
    #: (e.g. https://agentrouter.org for dev). Empty = api.anthropic.com.
    anthropic_base_url: str = ""
    #: Extra headers sent on every provider request, as a JSON object. Some
    #: gateways gate on headers the SDK does not set by default; keeping this in
    #: config means swapping gateways never means editing the adapter.
    anthropic_extra_headers: str = ""
    #: Kept configurable because the right model changes faster than this code does.
    anthropic_model: str = "claude-opus-4-8"
    #: Lookup is interactive — the client shows a spinner, so fail fast rather than
    #: leaving the "Writing the back…" state up indefinitely.
    anthropic_timeout_seconds: float = 30.0
    #: Ceiling per lookup/story response. Generous enough for 4 full card backs.
    anthropic_max_tokens: int = 4096

    #: Required when AI_PROVIDER=avalai. Never commit a value — set it in the
    #: environment. Rotate immediately if it leaks.
    avalai_api_key: str = ""
    #: AvalAI (https://avalai.ir) speaks the OpenAI protocol, not Anthropic's.
    avalai_base_url: str = "https://api.avalai.ir/v1"
    #: No sensible default across an arbitrary gateway's model catalogue —
    #: required explicitly when AI_PROVIDER=avalai (validated below).
    avalai_model: str = ""
    avalai_timeout_seconds: float = 30.0
    #: Ceiling per lookup/story response. Generous enough for 4 full card backs.
    avalai_max_tokens: int = 4096

    #: GapGPT (https://gapgpt.app), also OpenAI-protocol. Measured on 2026-08-17
    #: at ~4x AvalAI's median latency and ~3.5x its cost for identical output
    #: quality, which is why it is the fallback rather than the primary — but it
    #: was the *more reliable* of the two in 2026-07, so it earns its place.
    gapgpt_api_key: str = ""
    gapgpt_base_url: str = "https://api.gapgpt.app/v1"
    gapgpt_model: str = ""
    gapgpt_timeout_seconds: float = 30.0
    gapgpt_max_tokens: int = 4096

    #: OpenRouter (https://openrouter.ai). Model ids here are namespaced by
    #: upstream vendor (``google/gemini-3.5-flash-lite``), so this one's
    #: ``_MODEL`` cannot be copied from the others.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = ""
    openrouter_timeout_seconds: float = 30.0
    openrouter_max_tokens: int = 4096
    #: JSON object of extra headers, same shape and purpose as
    #: ``ANTHROPIC_EXTRA_HEADERS``. OpenRouter reads ``HTTP-Referer`` and
    #: ``X-Title`` for attribution on its public leaderboards.
    openrouter_extra_headers: str = ""

    # ── AI lookup cache ───────────────────────────────────────
    #: Vocabulary lookups repeat heavily across the user base, so the same
    #: provider response is otherwise bought over and over. Off only for
    #: debugging a provider in isolation — leaving it off in production means
    #: paying full price for every repeat of every word.
    ai_cache_enabled: bool = True
    #: How long an `unsupported` verdict is remembered. Real words never expire;
    #: unintelligible input does, so retry-spam is absorbed without letting the
    #: table fill with junk that will never be asked for again. 7 days.
    ai_cache_unsupported_ttl_seconds: int = 7 * 24 * 3600

    # ── Dictionary grounding ──────────────────────────────────
    #: Front lookups with a free English dictionary and ask the model only to
    #: select and translate its senses, instead of recalling them. Measured on
    #: our own prompts this cut input tokens ~60% and cost ~30%, and removed the
    #: invented senses the generative path produces for polysemous words.
    #: Off by default: it changes what every card says, so it is a deliberate
    #: product decision, not a deployment detail.
    dictionary_enabled: bool = False
    #: Hard ceiling on the dictionary call. It sits in front of a path that
    #: already works without it, so being slow is worth less than giving up:
    #: measured p95 is 325 ms, and anything past this falls through to full
    #: generation.
    dictionary_timeout_seconds: float = 1.0
    #: Where entries are cached. Defaults to the Celery broker's Redis — same
    #: server, separate logical database, so a cache flush cannot touch queued
    #: tasks.
    dictionary_redis_url: str = "redis://localhost:6379/1"
    #: Entries are immutable facts about English and shared by every learner, so
    #: they keep for a long time. This cache is the main defence against the
    #: API's rate limiting, not a latency optimisation.
    dictionary_cache_ttl_seconds: int = 30 * 24 * 3600
    #: Misses expire far sooner than hits: a word Wiktionary lacks today (new
    #: slang) may appear next month, and a 30-day negative cache would hide it.
    dictionary_cache_miss_ttl_seconds: int = 24 * 3600
    #: Whether the dictionary may be called for **pronunciation only**, without
    #: turning on grounding. These are two different decisions and were one flag
    #: for too long: grounding changes what every card *says* and is a product
    #: choice, while an IPA transcription is a free, factual lookup that changes
    #: nothing a learner reads except the line under the term. Coupling them
    #: meant a deck could not have pronunciation without being re-worded.
    #:
    #: ``DICTIONARY_ENABLED`` implies this — grounding already fetches the entry
    #: an IPA comes from, so an existing deployment's behaviour is unchanged.
    phonetics_enabled: bool = False
    #: Whether the grounded path also rewrites each dictionary definition into
    #: learner-dictionary English, instead of showing the source wording.
    #:
    #: Off means the cheapest possible call — the model returns only Persian
    #: headlines and the English is joined back locally, so it cannot corrupt
    #: text it never writes. The cost is that the card shows Wiktionary's own
    #: prose, which is accurate but written for reference, not for learners
    #: ("Sensitive mental touch; special skill or faculty; keen perception or
    #: discernment"). Turn it on when card readability matters more than the
    #: extra output tokens.
    dictionary_rewrite_definitions: bool = False
    #: How many distinct terms one ``vocably.ai.backfill_phonetics`` run looks
    #: up. Sized against the dictionary's rate limit rather than the size of the
    #: backlog: the job is nightly and unhurried, and a card without an IPA
    #: simply renders without one until its turn comes.
    phonetic_backfill_batch_size: int = Field(default=200, ge=1, le=5000)
    #: UTC hour of that run. Deliberately not the partition-maintenance hour —
    #: this one makes hundreds of outbound requests, and the two jobs have no
    #: reason to contend.
    phonetic_backfill_hour: int = Field(default=4, ge=0, le=23)

    # ── Lexicon (durable shared vocabulary knowledge) ─────────
    #: Whether lookups read from and write to the shared lexicon. On, this layer
    #: sits *inside* the lookup cache: repeat requests are still answered by the
    #: cache, and the lexicon answers the ones the cache cannot — which, the day
    #: a prompt version is bumped, is every one of them. Off means a prompt edit
    #: re-buys the entire corpus, so leave it on outside of debugging.
    lexicon_enabled: bool = True
    #: Where the single-flight lock lives. Its own logical database on the same
    #: Redis — deliberately **not** database 2, which the rate limiter owns:
    #: sharing one would let a lock key and a limiter key collide by name, and
    #: would make flushing the disposable one wipe a security control. It holds
    #: nothing durable, so flushing this one costs a few duplicated calls.
    lexicon_redis_url: str = "redis://localhost:6379/3"
    #: Deduplicate concurrent generation of the same word across workers and
    #: requests. Pure cost saving — correctness comes from the lexicon's unique
    #: constraints — so an unreachable Redis degrades to "both generate".
    lexicon_single_flight: bool = True

    # ── Deck build pipeline ───────────────────────────────────
    #: Words one ``vocably.ai.build_deck`` run resolves before re-queuing itself.
    #: This times worker concurrency *is* the rate limit on provider calls, which
    #: is why there is no token bucket anywhere in the pipeline.
    deck_build_batch_size: int = Field(default=25, ge=1, le=500)
    #: Pause between batches, in seconds. Spreads a 500-word build out rather
    #: than firing it at a provider as fast as the worker can loop.
    deck_build_batch_delay_seconds: int = Field(default=5, ge=0, le=600)
    #: How long a claimed item may sit untouched before another worker may take
    #: it — i.e. how long after a worker dies its word stays parked.
    deck_build_claim_timeout_seconds: int = Field(default=600, ge=60, le=3600)
    #: Phone number of the account that owns official pre-built decks. A real
    #: user row, because decks, memberships and cards all key off one; never a
    #: person, and deliberately not deletable through the account-deletion flow.
    content_owner_phone: str = "+000000000000"
    #: Rough price of one lookup-shaped provider call, used only to estimate a
    #: build's remaining spend on the admin screen. An estimate labelled as one,
    #: never billing.
    ai_cost_per_call_usd: float = 0.004

    # ── Review history ────────────────────────────────────────
    #: How many months of monthly partitions to keep ahead of today. The
    #: maintenance job (``make partitions``) keeps this window rolling; the
    #: margin is what stops a lapsed job from ever reaching the DEFAULT
    #: partition, so it is measured in months rather than days on purpose.
    review_history_partition_lookahead_months: int = 12
    #: How long raw review events are kept before their partition is dropped.
    #: Two years is well past what fitting a per-learner memory model needs,
    #: and long-range analytics should read rolled-up aggregates rather than
    #: raw events. ``0`` disables pruning entirely (keep everything).
    review_history_retention_months: int = 24
    #: Whether the scheduled maintenance run also *drops* partitions past the
    #: retention window. Off by default: enabling it means a background job
    #: deletes learners' history on its own, irreversibly. Turn it on once the
    #: retention window above is a deliberate policy rather than a default —
    #: while it is off, expired partitions are only reported, never removed.
    review_history_auto_prune: bool = False
    #: UTC hour of the daily partition-maintenance run. Creating partitions is
    #: cheap and lock-free, so the exact hour matters little; a quiet one is
    #: still preferable because pruning takes a brief exclusive lock.
    review_history_maintenance_hour: int = Field(default=3, ge=0, le=23)

    # ── Background tasks (Celery) ─────────────────────────────
    #: Broker URL. Redis by default; any broker Celery supports will do.
    celery_broker_url: str = "redis://localhost:6379/0"
    #: Where task results are stored. Empty (the default) disables result
    #: storage entirely — scheduled maintenance has no caller waiting on a
    #: return value, and keeping results would grow Redis for nothing. Set it
    #: when a task's result is actually read back.
    celery_result_backend: str = ""
    #: Hard kill / graceful-stop limits for a single task, in seconds. A task
    #: that outruns these is wedged, and holding a worker slot forever is worse
    #: than failing loudly.
    celery_task_time_limit_seconds: int = 600
    celery_task_soft_time_limit_seconds: int = 540

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dictionary_available(self) -> bool:
        """Whether anything may call the dictionary at all.

        Grounding implies pronunciation: the grounded path already fetches the
        entry an IPA comes from, so turning grounding on has never *not* meant
        the dictionary is reachable.
        """
        return self.dictionary_enabled or self.phonetics_enabled

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        """Accept a comma-separated string or a JSON/list for CORS origins."""
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_database_uri(self) -> str:
        """Async connection string; DATABASE_URL wins when explicitly provided."""
        if self.database_url:
            return self.database_url
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_enabled(self) -> bool:
        """Whether to mount Swagger UI, ReDoc and openapi.json. See ``expose_docs``."""
        if self.expose_docs is not None:
            return self.expose_docs
        return not self.is_production

    @model_validator(mode="after")
    def _validate_secret_key(self) -> Settings:
        """The one credential whose default is catastrophic rather than merely wrong.

        ``secret_key`` signs every access and refresh token *and* peppers the OTP
        hashes. Booting production with a value anyone can read in this repository
        means anyone can mint a token for any user id — including an admin's — with
        no request to this server at all. Nothing downstream would look unusual: the
        signature is valid, so every check that follows it passes.

        Length matters separately from secrecy: HS256 is HMAC-SHA256, so a key
        shorter than its 32-byte block is the weakest link in an otherwise sound
        chain, and PyJWT warns on every token issued with one.
        """
        if not self.is_production:
            return self
        placeholder = "change-me" in self.secret_key.lower()
        if placeholder or len(self.secret_key) < self.MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                "SECRET_KEY must be a random string of at least "
                f"{self.MIN_SECRET_KEY_LENGTH} characters when ENVIRONMENT=production "
                "— it signs every JWT and peppers every OTP hash, so a default or "
                "placeholder value lets anyone holding this source forge a token for "
                "any account. Generate one with `openssl rand -hex 32`. Rotating it "
                "signs every user out and invalidates OTPs already in flight."
            )
        return self

    @model_validator(mode="after")
    def _validate_debug(self) -> Settings:
        """``debug`` defaults to True, which is right for a laptop and wrong on a VPS.

        deploy/docker-compose.prod.yml already pins DEBUG=false for all three
        services, so this guards the case where that line is edited away or the app
        is run in production outside that compose file — not a live misconfiguration.
        """
        if self.is_production and self.debug:
            raise ValueError(
                "DEBUG must be false when ENVIRONMENT=production — it turns on "
                "verbose error surfaces that belong in a log, not in a response."
            )
        return self

    @model_validator(mode="after")
    def _validate_cors_origins(self) -> Settings:
        """``*`` and credentialed CORS are individually fine and together are not.

        app/main.py mounts CORSMiddleware with ``allow_credentials=True``. Starlette
        answers a wildcard origin by echoing back whatever Origin was sent the moment
        credentials are in play, so ``*`` here is not "any origin, anonymously" — it
        is "every origin on the internet, with the caller's cookies", which is the
        classic way a CSRF-proof API stops being one.

        Bearer tokens are what this app actually authenticates with, so the practical
        exposure today is small. The reason to refuse it at startup is that the day
        anything moves to a cookie, nothing else would notice.
        """
        if self.is_production and "*" in self.backend_cors_origins:
            raise ValueError(
                "BACKEND_CORS_ORIGINS must not contain '*' when ENVIRONMENT=production "
                "— the CORS middleware sends credentials, and a wildcard origin is "
                "echoed back per-request rather than genuinely anonymous. List the "
                "app's origins explicitly."
            )
        return self

    @model_validator(mode="after")
    def _validate_fixed_otp_code(self) -> Settings:
        if not self.otp_fixed_code:
            return self
        if self.is_production:
            raise ValueError(
                "OTP_FIXED_CODE must not be set when ENVIRONMENT=production — "
                "it disables OTP secrecy for every phone number."
            )
        if not self.otp_fixed_code.isdigit() or len(self.otp_fixed_code) != self.otp_length:
            raise ValueError(
                f"OTP_FIXED_CODE must be exactly {self.otp_length} digits (OTP_LENGTH)."
            )
        return self

    @model_validator(mode="after")
    def _validate_celery_time_limits(self) -> Settings:
        if self.celery_task_soft_time_limit_seconds >= self.celery_task_time_limit_seconds:
            raise ValueError(
                "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS must be below "
                "CELERY_TASK_TIME_LIMIT_SECONDS, or the task is killed outright "
                "before it ever gets the chance to shut down cleanly."
            )
        return self

    @model_validator(mode="after")
    def _validate_google_verifier(self) -> Settings:
        if self.is_production and self.google_verifier != "google":
            raise ValueError(
                "GOOGLE_VERIFIER must be 'google' when ENVIRONMENT=production — the "
                "'stub' verifier trusts any caller-supplied string as a Google identity."
            )
        return self

    @model_validator(mode="after")
    def _validate_ai_provider(self) -> Settings:
        if self.ai_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
        # Every gateway in the chain, primary included, must be usable at boot.
        # A fallback whose key is missing is worse than no fallback: it looks
        # like resilience, and only reveals itself the minute the primary dies.
        for name in self.provider_chain:
            self.provider_profile(name)
        self.anthropic_header_map  # noqa: B018 — fail fast on malformed JSON at startup
        return self

    @property
    def provider_chain(self) -> list[str]:
        """The gateways to try, in order, primary first.

        Empty for ``stub`` and ``anthropic``: neither is an OpenAI-protocol
        gateway, so neither participates in failover. Duplicates are dropped
        rather than rejected — naming the primary again in the fallback list is
        a config typo whose only effect would be calling a dead gateway twice.
        """
        if self.ai_provider in ("stub", "anthropic"):
            return []
        names: list[str] = [self.ai_provider]
        for raw in self.ai_fallback_providers.split(","):
            name = raw.strip().lower()
            if name and name not in names:
                names.append(name)
        return names

    def provider_profile(self, name: str) -> ProviderProfile:
        """Resolve one gateway's settings, raising if it is not usable.

        Raises rather than returning ``None`` because every caller — the boot
        validator and the factory alike — treats an unusable gateway as fatal,
        and a factory discovering it mid-request would surface as a 500 on a
        learner's lookup instead of a refused deploy.
        """
        if name not in OPENAI_PROTOCOL_PROVIDERS:
            known = ", ".join(OPENAI_PROTOCOL_PROVIDERS)
            raise ValueError(f"Unknown AI provider {name!r}. Known gateways: {known}.")
        prefix = name.upper()
        key = str(getattr(self, f"{name}_api_key", ""))
        model = str(getattr(self, f"{name}_model", ""))
        if not key:
            raise ValueError(f"AI provider {name!r} requires {prefix}_API_KEY.")
        if not model:
            # No sensible default across an arbitrary gateway's catalogue, and
            # guessing one would spend money on the wrong model silently.
            raise ValueError(f"AI provider {name!r} requires {prefix}_MODEL.")
        return ProviderProfile(
            name=name,
            api_key=key,
            model=model,
            base_url=str(getattr(self, f"{name}_base_url", "")),
            timeout_seconds=float(getattr(self, f"{name}_timeout_seconds", 30.0)),
            max_tokens=int(getattr(self, f"{name}_max_tokens", 4096)),
            extra_headers=self._header_map(f"{name}_extra_headers"),
        )

    @property
    def anthropic_header_map(self) -> dict[str, str]:
        """``anthropic_extra_headers`` parsed, or ``{}`` when unset."""
        return self._header_map("anthropic_extra_headers")

    def _header_map(self, field: str) -> dict[str, str]:
        """Parse one ``*_EXTRA_HEADERS`` field, or ``{}`` when unset or absent."""
        raw = str(getattr(self, field, "") or "")
        if not raw.strip():
            return {}
        env_name = field.upper()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{env_name} must be a JSON object: {exc}") from None
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            raise ValueError(f"{env_name} must be a JSON object of string values.")
        return parsed


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()


settings = get_settings()
