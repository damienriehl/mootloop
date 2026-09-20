"use strict";
(() => {
  const main = document.getElementById("content");
  const labels = {
    synthetic: "Fictional litigation",
    "public-record": "Historical counterfactuals",
    business: "Business counsel",
  };
  let navigation = 0;
  let controller;
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function link(text, href) {
    const node = el("a", text);
    if (
      (href.startsWith("/") && !href.startsWith("//")) ||
      /^#[a-z][a-z-]*$/.test(href)
    )
      node.href = href;
    else {
      try {
        const url = new URL(href);
        if (url.protocol !== "https:" || url.username || url.password)
          return el("span", text);
        node.href = url.href;
        node.rel = "noreferrer";
      } catch {
        return el("span", text);
      }
    }
    return node;
  }
  function prose(text) {
    const body = el("div", undefined, "prose");
    for (const block of text.split(/\n\s*\n/)) {
      const heading = block.match(/^#{1,6}\s+([^\n]+)\n?([\s\S]*)$/);
      if (heading) {
        body.append(el("h3", heading[1]));
        if (heading[2]) body.append(el("p", heading[2]));
      } else body.append(el("p", block));
    }
    return body;
  }
  function section(id, title, text) {
    const node = el("section");
    node.id = id;
    node.append(el("h2", title));
    if (text) node.append(prose(text));
    return node;
  }
  async function get(path, signal) {
    const response = await fetch(path, {
      signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok)
      throw new Error(
        response.status === 404
          ? "This demo or revision is unavailable. Return to the library to see the current examples."
          : "The reviewed demo release could not be loaded. Please try again later.",
      );
    return response.json();
  }
  function localUse() {
    const node = section("local", "Use MootLoop locally");
    node.className = "local-use";
    node.append(
      el(
        "p",
        "Install MootLoop on your computer to explore a prepared replay or work with your own materials. The public library does not run models or accept documents.",
      ),
    );
    node.append(
      link(
        "Installation and local workflow guide",
        "https://github.com/damienriehl/mootloop/blob/main/docs/demos/local-use.md",
      ),
    );
    node.append(
      el(
        "pre",
        "git clone https://github.com/damienriehl/mootloop.git\ncd mootloop\nuv sync\nuv run mootloop --help",
      ),
    );
    node.append(
      el(
        "p",
        "Requires Python 3.12 and uv. Each example includes a downloadable input bundle and its own replay commands. Manual prompt-and-record workflows can produce different results.",
      ),
    );
    return node;
  }
  function catalogView(catalog) {
    document.title = "Demo library | MootLoop";
    main.replaceChildren(
      el("h1", "Demo library"),
      el(
        "p",
        "See how legal work changes when it is challenged. Explore fictional litigation, alternative strategies in public cases, and questions from business teams to their lawyers.",
        "intro",
      ),
      el(
        "p",
        "Prepared, read-only examples. Agent-authored scripts are replayed without live model calls. Review gates and limitations remain visible; no example carries attorney approval.",
        "disclosure",
      ),
    );
    const layout = el("div", undefined, "catalog-layout");
    const filters = el("form", undefined, "filters");
    filters.setAttribute("aria-label", "Filter demos");
    filters.addEventListener("submit", (event) => event.preventDefault());
    const results = el("div");
    const count = el("p", "", "result-count");
    count.setAttribute("role", "status");
    const groups = el("div");
    results.append(count, groups);
    const params = new URLSearchParams(location.search);
    const controls = {};
    const fields = [
      ["q", "Search demos"],
      ["collection", "Collection"],
      ["practice_area", "Practice area"],
      ["work_product", "Work product"],
      ["court_level", "Court setting"],
    ];
    for (const [key, title] of fields) {
      const wrapper = el(
        "div",
        undefined,
        key === "q" ? "search-field" : "filter-field",
      );
      const label = el("label", title);
      label.htmlFor = `filter-${key}`;
      const control = el(key === "q" ? "input" : "select");
      control.id = label.htmlFor;
      if (key === "q") {
        control.type = "search";
        control.placeholder = "Case, question or jurisdiction";
      } else {
        const all = el("option", "All");
        all.value = "";
        control.append(all);
        for (const value of [
          ...new Set(catalog.entries.map((e) => e.descriptor[key])),
        ].sort()) {
          const option = el(
            "option",
            key === "collection" ? labels[value] : value.replaceAll("-", " "),
          );
          option.value = value;
          control.append(option);
        }
      }
      control.value = params.get(key) || "";
      controls[key] = control;
      wrapper.append(label, control);
      filters.append(wrapper);
      control.addEventListener(key === "q" ? "input" : "change", () =>
        update(true),
      );
    }
    const reset = el("button", "Clear filters");
    reset.type = "button";
    reset.addEventListener("click", () => {
      Object.values(controls).forEach((c) => (c.value = ""));
      update(true);
    });
    filters.append(reset);
    function update(writeURL) {
      const query = controls.q.value.toLocaleLowerCase().trim();
      const visible = catalog.entries.filter((entry) => {
        const d = entry.descriptor;
        return (
          (!query ||
            [
              d.title,
              d.introduction,
              d.jurisdiction,
              d.practice_area,
              d.work_product,
            ]
              .join(" ")
              .toLocaleLowerCase()
              .includes(query)) &&
          fields
            .slice(1)
            .every(
              ([key]) => !controls[key].value || controls[key].value === d[key],
            )
        );
      });
      count.textContent = `${visible.length} of ${catalog.entries.length} demos`;
      groups.replaceChildren();
      if (!visible.length)
        groups.append(
          el(
            "p",
            "No demos match these filters. Try another term or clear the filters.",
          ),
        );
      for (const [collection, title] of Object.entries(labels)) {
        const entries = visible.filter(
          (e) => e.descriptor.collection === collection,
        );
        if (!entries.length) continue;
        const group = el("section", undefined, "collection-group");
        group.append(el("h2", title));
        const list = el("ul", undefined, "demo-list");
        for (const entry of entries) {
          const d = entry.descriptor;
          const row = el("li");
          row.append(
            link(
              d.title,
              `/demos/${encodeURIComponent(d.demo_id)}?revision=${encodeURIComponent(entry.revision)}`,
            ),
          );
          row.append(
            el(
              "p",
              `${d.work_product} · ${d.jurisdiction} · ${d.court_level.replaceAll("-", " ")}`,
              "metadata",
            ),
          );
          row.append(
            el(
              "p",
              d.collection === "public-record"
                ? `Two strategy trails. Historical cutoff: ${d.cutoff}.`
                : d.introduction,
            ),
          );
          list.append(row);
        }
        group.append(list);
        groups.append(group);
      }
      if (writeURL) {
        const values = new URLSearchParams();
        for (const [key] of fields)
          if (controls[key].value) values.set(key, controls[key].value);
        history.replaceState(
          null,
          "",
          `/demos/${values.size ? "?" + values : ""}`,
        );
      }
    }
    layout.append(filters, results);
    main.append(layout, localUse());
    update(false);
  }
  function detailView(snapshot, selected, token, signal) {
    const d = snapshot.descriptor;
    const strategies = snapshot.strategies;
    const strategy =
      strategies.find((s) => s.strategy_id === selected) || strategies[0];
    document.title = `${d.title} | MootLoop`;
    main.replaceChildren(
      link("All demos", "/demos/"),
      el("h1", d.title),
      el(
        "p",
        `${labels[d.collection]} · ${d.work_product} · ${d.jurisdiction} · ${d.court_level.replaceAll("-", " ")}`,
        "metadata",
      ),
      el("p", d.introduction, "intro"),
    );
    if (d.cutoff)
      main.append(
        el(
          "p",
          `Historical input cutoff: ${d.cutoff}. Represented side: ${d.represented_side}. Later outcomes are separate context.`,
        ),
      );
    main.append(
      el(
        "p",
        `${snapshot.provenance.authorship} ${snapshot.provenance.preparation === "scripted-replay" ? "Deterministic scripted replay." : "Prepared model output."} ${snapshot.provenance.provider_calls} provider calls. No attorney approval.`,
        "disclosure",
      ),
    );
    const provenance = el("details");
    provenance.append(
      el("summary", "Preparation and editorial limits"),
      prose(snapshot.provenance.editorial_changes),
      prose(snapshot.provenance.limitations.join("\n\n")),
    );
    main.append(provenance);
    if (strategies.length > 1) {
      const picker = el("div", undefined, "strategy-picker");
      const label = el("label", "Choose a strategy");
      label.htmlFor = "strategy";
      const select = el("select");
      select.id = "strategy";
      for (const s of strategies) {
        const option = el("option", s.title);
        option.value = s.strategy_id;
        select.append(option);
      }
      select.value = strategy.strategy_id;
      select.addEventListener("change", () => {
        const url = new URL(location.href);
        url.searchParams.set("strategy", select.value);
        history.pushState(null, "", url);
        detailView(snapshot, select.value, token, signal);
        document.getElementById("strategy").focus();
      });
      picker.append(label, select);
      main.append(picker);
    }
    const layout = el("div", undefined, "reader-layout");
    const nav = el("nav", undefined, "reader-nav");
    nav.setAttribute("aria-label", "On this page");
    const reading = el("div", undefined, "reading");
    const anchors = [
      ["inputs", "Inputs and assumptions"],
      ["initial", "Initial draft"],
      ["critique", "Adversarial critique"],
      ["revised", "Revised work product"],
      ["assessment", "Assessment"],
      ["gates", "Review gates"],
    ];
    if (snapshot.comparison)
      anchors.push(
        ["comparison", "Compare strategies"],
        ["historical", "Actual outcome"],
      );
    anchors.push(["sources", "Sources and support"], ["local", "Use locally"]);
    for (const [id, title] of anchors) {
      const a = link(title, `#${id}`);
      a.addEventListener("click", () => {
        const target = document.getElementById(id);
        if (target?.tagName === "DETAILS") target.open = true;
      });
      nav.append(a);
    }
    const input = section("inputs", strategy.title);
    input.append(prose(strategy.input_summary));
    if (strategy.assumptions.length) {
      input.append(el("h3", "Assumptions"));
      const list = el("ul");
      strategy.assumptions.forEach((a) => list.append(el("li", a)));
      input.append(list);
    }
    reading.append(input);
    const stageNames = {
      initial: "Initial draft",
      critique: "Adversarial critique",
      revised: "Revised work product",
      assessment: "Assessment",
    };
    for (const stage of strategy.stages) {
      const details = el("details", undefined, "stage");
      details.id = stage.kind;
      details.dataset.kind = stage.kind;
      details.open = stage.kind === "revised" || stage.kind === "assessment";
      details.append(el("summary", stageNames[stage.kind]), prose(stage.text));
      reading.append(details);
    }
    const gates = section("gates", "Review gates");
    gates.append(
      el(
        "p",
        strategy.gate_state.export_ready
          ? "The recorded export gate is ready. This does not constitute attorney approval of the demonstration."
          : "Not ready for clean export. The recorded run retains the following blockers: " +
              strategy.gate_state.blockers.join(", ") +
              ".",
      ),
    );
    gates.append(
      el(
        "p",
        `Recorded run status: ${strategy.gate_state.run_status}. Scripted rubric results describe this preparation, not measured legal performance.`,
      ),
    );
    const gateDetails = el("details");
    gateDetails.append(el("summary", "See recorded gate results"));
    const gateList = el("ul", undefined, "gate-list");
    Object.entries(strategy.gate_state.results).forEach(([key, value]) =>
      gateList.append(el("li", `${key}: ${value.replaceAll("_", " ")}`)),
    );
    gateDetails.append(gateList);
    gates.append(gateDetails);
    reading.append(gates);
    if (snapshot.comparison)
      reading.append(
        section(
          "comparison",
          "How the strategies could change the outcome",
          snapshot.comparison,
        ),
      );
    if (snapshot.actual_outcome) {
      const history = section(
        "historical",
        "Actual outcome: historical context",
        snapshot.actual_outcome,
      );
      history.className = "historical";
      history.append(
        el(
          "p",
          "This later result was excluded from the strategy inputs. It does not establish what a counterfactual strategy would have achieved.",
        ),
      );
      for (const id of snapshot.outcome_source_ids) {
        const source = snapshot.sources.find((s) => s.source_id === id);
        if (source) history.append(link(source.title, source.url));
      }
      reading.append(history);
    }
    const sources = section("sources", "Sources and claim support");
    const list = el("ol", undefined, "source-list");
    for (const source of snapshot.sources) {
      const item = el("li");
      item.append(
        link(source.title, source.url),
        el(
          "p",
          `${source.source_id}: ${source.classification}. Available: ${source.available_on || "not established"}. ${source.locator}`,
          "metadata",
        ),
      );
      const detail = el("details");
      detail.append(
        el("summary", "Source provenance"),
        el("p", source.redistribution_basis),
        el("p", `Retrieved: ${source.retrieved_on}. SHA-256: ${source.sha256}`),
      );
      item.append(detail);
      list.append(item);
    }
    sources.append(list);
    const claims = el("details");
    claims.append(el("summary", "Trace material claims to sources"));
    for (const claim of snapshot.claims) {
      claims.append(
        el("p", claim.claim),
        el(
          "p",
          `${claim.classification}; ${claim.source_ids.join(", ")}; ${claim.locator}`,
          "metadata",
        ),
      );
    }
    sources.append(claims);
    reading.append(sources);
    const local = localUse();
    const base = `/api/demos/${encodeURIComponent(d.demo_id)}/${encodeURIComponent(snapshot.revision)}/inputs`;
    local.append(
      link("Download this revision’s input bundle", base),
      el("pre", snapshot.local_instructions),
    );
    const inspect = el("button", "Inspect the complete local input bundle");
    inspect.type = "button";
    const bundle = el("div");
    inspect.addEventListener("click", async () => {
      inspect.disabled = true;
      bundle.replaceChildren(el("p", "Loading inputs…"));
      try {
        const data = await get(base, signal);
        if (token !== navigation || !bundle.isConnected) return;
        bundle.replaceChildren(el("pre", JSON.stringify(data, null, 2)));
      } catch (error) {
        if (
          error.name !== "AbortError" &&
          token === navigation &&
          bundle.isConnected
        )
          bundle.replaceChildren(el("p", error.message));
      } finally {
        inspect.disabled = false;
      }
    });
    local.append(inspect, bundle);
    reading.append(local);
    layout.append(nav, reading);
    main.append(layout);
  }
  async function route() {
    const token = ++navigation;
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    main.replaceChildren(el("p", "Loading the demo library…"));
    main.firstChild.setAttribute("role", "status");
    try {
      const parts = location.pathname.split("/").filter(Boolean);
      const id = parts[1];
      if (!id) {
        const catalog = await get("/api/demos", signal);
        if (token === navigation) catalogView(catalog);
      } else {
        const params = new URLSearchParams(location.search);
        const revision = params.get("revision");
        const path = `/api/demos/${encodeURIComponent(decodeURIComponent(id))}${revision ? "/" + encodeURIComponent(revision) : ""}`;
        const snapshot = await get(path, signal);
        if (token !== navigation) return;
        if (
          snapshot.descriptor.demo_id !== decodeURIComponent(id) ||
          (revision && snapshot.revision !== revision)
        )
          throw new Error("The returned demo does not match this link.");
        if (!revision) {
          params.set("revision", snapshot.revision);
          history.replaceState(
            null,
            "",
            `${location.pathname}?${params}${location.hash}`,
          );
        }
        detailView(snapshot, params.get("strategy"), token, signal);
      }
      if (token === navigation && location.hash)
        document.getElementById(location.hash.slice(1))?.scrollIntoView();
    } catch (error) {
      if (error.name === "AbortError" || token !== navigation) return;
      const alert = el("p", error.message);
      alert.setAttribute("role", "alert");
      main.replaceChildren(
        el("h1", "Demo unavailable"),
        alert,
        link("Return to the demo library", "/demos/"),
      );
    }
  }
  document.addEventListener("click", (event) => {
    const a = event.target.closest?.("a");
    if (
      !a ||
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      a.target ||
      a.hasAttribute("download")
    )
      return;
    const url = new URL(a.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/demos/"))
      return;
    if (
      url.pathname === location.pathname &&
      url.search === location.search &&
      url.hash
    )
      return;
    event.preventDefault();
    history.pushState(null, "", url);
    route();
    window.scrollTo(0, 0);
    main.focus();
  });
  window.addEventListener("popstate", route);
  route();
})();
