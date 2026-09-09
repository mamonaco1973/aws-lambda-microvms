# One row per tenant ("alice" / "bob") holding the MicroVM id, endpoint and the
# last application sample. Interpreter state never leaves the MicroVM itself.
resource "aws_dynamodb_table" "state" {
  name         = "${var.name}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
}
