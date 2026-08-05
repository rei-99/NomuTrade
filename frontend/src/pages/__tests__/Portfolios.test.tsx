import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../auth";
import { I18nProvider } from "../../i18n";
import { Portfolios } from "../Portfolios";
import { makePortfolio } from "../../test/utils";

vi.mock("../../api/client", () => ({ api: vi.fn() }));
vi.mock("../../auth", () => ({ useAuth: vi.fn() }));

const PFS = [
  makePortfolio(),
  makePortfolio({ portfolio_id: "pf-2", name: "Client Book", type: "CLIENT", cash_balance: 500_000, total_value: 510_000 }),
];

function stubList() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/portfolios") return { items: PFS, next_cursor: null };
    throw new Error(`unexpected api call ${path}`);
  });
}

function renderPage(canSubmit = true) {
  vi.mocked(useAuth).mockReturnValue({ hasPerm: () => canSubmit } as never);
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={["/portfolios"]}>
        <Routes>
          <Route path="/portfolios" element={<Portfolios />} />
          <Route path="/portfolios/:id" element={<div>DetailMarker</div>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("Portfolios page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(useAuth).mockReset();
    stubList();
  });

  it("lists portfolios with type badges and values; row click opens the detail", async () => {
    renderPage();
    await screen.findByText("Alpha Book");
    expect(screen.getByText("Client Book")).toBeInTheDocument();
    expect(screen.getByText("HOUSE")).toBeInTheDocument();
    expect(screen.getByText("$500,000.00")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Client Book"));
    expect(await screen.findByText("DetailMarker")).toBeInTheDocument();
  });

  it("hides the create button without the admin permission (ROLE_MANAGE)", async () => {
    renderPage(false);
    await screen.findByText("Alpha Book");
    expect(screen.queryByRole("button", { name: "New portfolio" })).not.toBeInTheDocument();
  });

  it("create modal posts an object body and refreshes the list", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "New portfolio" }));

    const create = screen.getByRole("button", { name: "Create" });
    expect(create).toBeDisabled(); // blank name

    fireEvent.change(screen.getByPlaceholderText("e.g. Alpha Book"), { target: { value: " Gamma Book " } });
    fireEvent.change(screen.getByPlaceholderText("1,000,000"), { target: { value: "250000" } });
    fireEvent.click(create);

    // modal closes and the list refreshes
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const post = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "POST");
    expect(post?.[0]).toBe("/portfolios");
    // Regression guard: body must be the plain object — api() JSON-encodes it
    // itself; a pre-stringified body double-encodes and the server 422s.
    expect(post?.[1]?.body).toEqual({ name: "Gamma Book", initial_cash: 250000 });
  });

  it("omits initial_cash when the field is blank", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "New portfolio" }));
    fireEvent.change(screen.getByPlaceholderText("e.g. Alpha Book"), { target: { value: "Delta" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const post = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "POST");
    expect(post?.[1]?.body).toEqual({ name: "Delta" });
  });
});
