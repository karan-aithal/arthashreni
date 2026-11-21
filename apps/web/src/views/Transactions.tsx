import { Num } from "@/components/M";
import type { Imports, Transactions } from "@/lib/types";

export function TransactionsView({ t, imports }: { t: Transactions; imports: Imports }) {
  return (
    <>
      <h1>Transactions and imports</h1>
      <p>{t.dataset} dataset. The effective ledger view (corrections applied); showing {t.items.length} of {t.total}.</p>
      <div className="scroll"><table>
        <caption>Transactions</caption>
        <thead><tr><th scope="col">Date</th><th scope="col">Type</th><th scope="col">Asset</th><th scope="col">Account</th><th scope="col">Quantity</th><th scope="col">Price</th><th scope="col">Fees</th><th scope="col">Net cash (INR)</th></tr></thead>
        <tbody>{t.items.map((x) => (
          <tr key={x.id}><td>{x.trade_date}</td><td>{x.type}{x.corrects_id ? ` (corrects #${x.corrects_id})` : ""}</td><td>{x.asset ?? "—"}</td><td>{x.account}</td>
            <td><Num m={x.quantity} dp={3} /></td><td><Num m={x.price} dp={4} /></td><td><Num m={x.fees} dp={2} /></td><td><Num m={x.net_cash_inr} kind="inr" /></td></tr>))}
        </tbody>
      </table></div>
      <h2>Imports</h2>
      <p>Committing or rejecting an import stays in the CLI.</p>
      <div className="scroll"><table>
        <caption>Import batches</caption>
        <thead><tr><th scope="col">#</th><th scope="col">File</th><th scope="col">Kind</th><th scope="col">Status</th><th scope="col">New</th><th scope="col">Duplicate</th><th scope="col">Ambiguous</th><th scope="col">Rejected</th></tr></thead>
        <tbody>{imports.items.map((b) => (
          <tr key={b.id}><td>{b.id}</td><th scope="row">{b.file_name}</th><td>{b.source_kind}</td><td>{b.status}</td>
            <td>{b.rows.new}</td><td>{b.rows.duplicate}</td><td>{b.rows.ambiguous}</td><td>{b.rows.rejected}</td></tr>))}
        </tbody>
      </table></div>
    </>
  );
}
