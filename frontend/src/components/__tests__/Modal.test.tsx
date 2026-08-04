import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../../i18n";
import { Modal } from "../Modal";

function renderModal(props: Partial<Parameters<typeof Modal>[0]> = {}) {
  const onClose = props.onClose ?? vi.fn();
  render(
    <I18nProvider>
      <Modal title={props.title ?? "Details"} onClose={onClose} footer={props.footer} wide={props.wide}>
        {props.children ?? <p>body content</p>}
      </Modal>
    </I18nProvider>,
  );
  return onClose;
}

describe("Modal", () => {
  it("renders title, body and the dialog role", () => {
    renderModal();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Details")).toBeInTheDocument();
    expect(screen.getByText("body content")).toBeInTheDocument();
  });

  it("closes on Escape", () => {
    const onClose = renderModal();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("ignores other keys", () => {
    const onClose = renderModal();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("removes the Escape listener on unmount", () => {
    const onClose = vi.fn();
    const { unmount } = render(
      <I18nProvider>
        <Modal title="t" onClose={onClose}>
          x
        </Modal>
      </I18nProvider>,
    );
    unmount();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("backdrop mousedown closes, content mousedown does not", () => {
    const onClose = renderModal();
    const overlay = document.querySelector(".modal-overlay")!;
    const dialog = screen.getByRole("dialog");

    fireEvent.mouseDown(dialog); // inside the modal
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.mouseDown(overlay); // the backdrop itself
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("the header close button closes", () => {
    const onClose = renderModal();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders the footer only when provided", () => {
    const { unmount } = render(
      <I18nProvider>
        <Modal title="t" onClose={vi.fn()} footer={<button>Save it</button>}>
          x
        </Modal>
      </I18nProvider>,
    );
    expect(screen.getByText("Save it")).toBeInTheDocument();
    expect(document.querySelector(".modal-footer")).not.toBeNull();
    unmount();

    renderModal();
    expect(document.querySelector(".modal-footer")).toBeNull();
  });

  it("applies the wide class when wide", () => {
    renderModal({ wide: true });
    expect(screen.getByRole("dialog")).toHaveClass("modal-wide");
  });
});
