# ==============================================================================
# Session Table — which MicroVM belongs to which tenant
# ==============================================================================
# One row per tenant ("alice" / "bob") holding the MicroVM id, its endpoint and
# the last application sample. A shared row would let concurrent writes clobber
# each other, and a lost MicroVM id orphans a VM that bills until it expires.
#
# This table does not restore sessions. Interpreter memory lives in the MicroVM
# snapshot; what is stored here is only enough to find the VM again.

resource "aws_dynamodb_table" "state" {
  name = "${var.name}-state"

  # Two rows and a handful of requests per demo; provisioned capacity would be
  # pure idle cost.
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "id"
  attribute {
    name = "id"
    type = "S"
  }
}
