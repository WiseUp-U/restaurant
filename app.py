from flask import Flask, render_template, request, session, redirect, url_for
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import os
from werkzeug.utils import secure_filename
import uuid
from dotenv import load_dotenv
from functools import wraps
from flask_wtf.csrf import CSRFProtect

load_dotenv()

app = Flask(__name__)

DATABASE = "restaurant.db"

app.secret_key = os.environ["SECRET_KEY"]

csrf = CSRFProtect(app)

ALLOWED_IMAGE_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp"
}

UPLOAD_FOLDER = os.path.join(
    app.root_path,
    "static",
    "images"
    )
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def save_uploaded_image(image_file):

    if not image_file or not image_file.filename:
        return None

    original_filename = secure_filename(
        image_file.filename
    )

    if "." not in original_filename:
        return None

    extension = original_filename.rsplit(
        ".",
        1
    )[1].lower()

    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return None

    unique_filename = (
        uuid.uuid4().hex
        + "."
        + extension
    )

    image_file.save(
        os.path.join(
            app.config["UPLOAD_FOLDER"],
            unique_filename
        )
    )

    return unique_filename

def get_db():

    db = sqlite3.connect(DATABASE)

    db.row_factory = sqlite3.Row

    db.execute("PRAGMA foreign_keys = ON")

    return db


def admin_required(view):

    @wraps(view)
    def wrapped_view(*args, **kwargs):

        if "admin_id" not in session:
            return redirect(url_for("admin_login"))

        return view(*args, **kwargs)

    return wrapped_view


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if request.method == "GET":
        return render_template("admin_login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        return render_template(
            "admin_login.html",
            error="Please enter your username and password."
        )

    db = get_db()

    admin = db.execute("""
    SELECT id, username, password_hash
    FROM admins
    WHERE username = ?
    """,(username,)).fetchone()

    db.close()

    if admin is None:
        return render_template(
            "admin_login.html",
            error="Invalid username or password."
        )
    if not check_password_hash(
        admin["password_hash"],
        password
    ):
        return render_template(
            "admin_login.html",
            error="Invalid username or password."
        )
    
    session["admin_id"] = admin["id"]
    session["admin_username"] = admin["username"]

    return redirect(url_for("admin_dashboard"))


@app.route("/admin")
@admin_required
def admin_dashboard():

    db = get_db()

    # Total number of orders
    total_orders = db.execute("""
        SELECT COUNT(*) AS count
        FROM orders
    """).fetchone()["count"]


    # Orders currently being processed
    active_orders = db.execute("""
        SELECT COUNT(*) AS count
        FROM orders
        WHERE status IN (
            'confirmed',
            'preparing',
            'ready'
        )
    """).fetchone()["count"]


    # Today's sales
    today_sales = db.execute("""
        SELECT COALESCE(SUM(total), 0) AS total
        FROM orders
        WHERE payment_status = 'paid'
        AND status != 'cancelled'
        AND date(created_at) = date('now', 'localtime')
    """).fetchone()["total"]


    # Recent orders
    recent_orders = db.execute("""
        SELECT
            id,
            customer_name,
            total,
            status,
            payment_status,
            created_at
        FROM orders
        ORDER BY created_at DESC
        LIMIT 5
    """).fetchall()


    db.close()


    return render_template(
        "admin_dashboard.html",
        total_orders=total_orders,
        active_orders=active_orders,
        today_sales=today_sales,
        recent_orders=recent_orders
    )


@app.route("/admin/orders")
@admin_required
def admin_orders():

    db = get_db()

    orders = db.execute("""
    SELECT 
    id,
    tracking_code,
    customer_name,
    phone,
    total,
    status,
    payment_status,
    created_at
    FROM orders
    ORDER BY created_at DESC
    """).fetchall()

    db.close()

    return render_template(
        "admin_orders.html",
        orders=orders
    )


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def admin_update_order_status(order_id):

    new_status = request.form.get("status")

    allowed_statuses = {
        "preparing",
        "ready",
        "completed"
    }

    if new_status not in allowed_statuses:
        return "Invalid status.", 400

    db = get_db()

    order = db.execute("""
    SELECT id, status, payment_status
    FROM orders
    WHERE id = ?
    """, (order_id,)).fetchone()

    if order is None:
        db.close()
        return "Order not found", 404

    # Only paid orders can move through the restaurant workflow.
    if order["payment_status"] != "paid":
        db.close()
        return "Order has not been paid", 400

    current_status = order["status"]

    valid_transitions = {
        "confirmed": "preparing",
        "preparing": "ready",
        "ready": "completed"
    }

    if valid_transitions.get(current_status) != new_status:
        db.clsoe()
        return "Invalid status transition.", 400

    db.execute("""
    UPDATE orders
    SET status = ?
    WHERE id= ?
    """, (new_status, order_id))

    db.commit()
    db.close()

    return redirect(url_for("admin_orders"))


@app.route("/admin/orders/<int:order_id>")
@admin_required
def admin_order_detail(order_id):

    db = get_db()

    order = db.execute("""
    SELECT
    id,
    tracking_code,
    customer_name,
    phone,
    address,
    subtotal,
    delivery_fee,
    total,
    status,
    payment_status,
    payment_id,
    created_at
    FROM orders
    WHERE id = ?
    """, (order_id,)).fetchone()


    if order is None:
        db.close()
        return "Order not found", 404

    items = db.execute("""
    SELECT
    order_items.quantity,
    order_items.price,
    foods.name
    FROM order_items
    JOIN foods
    ON order_items.food_id = foods.id
    WHERE order_items.order_id = ?
    """, (order_id,)).fetchall()

    db.close()

    return render_template(
        "admin_order_detail.html",
        order=order,
        items=items
    )


@app.route("/admin/foods")
@admin_required
def admin_foods():

    db = get_db()

    foods = db.execute("""
    SELECT
    foods.id,
    foods.name,
    foods.description,
    foods.price,
    foods.image,
    foods.available,
    categories.name AS category_name
    FROM foods
    JOIN categories
    ON foods.category_id = categories.id
    ORDER BY categories.name, foods.name
    """).fetchall()

    db.close()

    return render_template(
        "admin_foods.html",
        foods=foods
    )


@app.route("/admin/foods/<int:food_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_food(food_id):

    db= get_db()

    food = db.execute("""
    SELECT id, available
    FROM foods
    WHERE id = ?
    """, (food_id,)).fetchone()

    if food is None:
        db.close()
        return "Food not found.", 404

    new_status = 0 if food["available"] else 1

    db.execute("""
    UPDATE foods
    SET available = ?
    WHERE id = ?
    """, (new_status, food_id))

    db.commit()
    db.close()

    return redirect(url_for("admin_foods"))


@app.route("/admin/foods/add", methods=["GET", "POST"])
@admin_required
def admin_add_food():

    db = get_db()

    categories = db.execute("""
    SELECT id, name
    FROM categories
    ORDER BY name
    """).fetchall()

    if request.method == "GET":
        db.close()

        return render_template(
            "admin_add_food.html",
            categories=categories
        )

    name = request.form.get("name","").strip()
    description = request.form.get("description", "").strip()
    price = request.form.get("price", "").strip()
    image_file = request.files.get("image")
    category_id = request.form.get("category_id", "").strip()

    if not name or not price or not category_id:
        db.close()

        return render_template(
            "admin_add_food.html",
            categories=categories,
            error="Name, price and category are required."
        )

    try:
        price = int(price)
        category_id = int(category_id)
    except ValueError:
        db.close()

        return render_template(
            "admin_add_food.html",
            categories=categories,
            error="Price and category must be valid numbers."
        )

    if price < 0:
        db.close()

        return render_template(
            "admin_add_food.html",
            categories=categories,
            error="Price cannot be negative."
        )

    category = db.execute("""
    SELECT id
    FROM categories
    WHERE id = ?
    """, (category_id,)).fetchone()

    if category is None:
        db.close()

        return render_template(
            "admin_add_food.html",
            categories=categories,
            error="Invalid category."
        )

    image_filename = None

    if image_file and image_file.filename:

        image_filename = save_uploaded_image(image_file)

        if image_filename is None:
            db.close()

            return render_template("admin_add_food.html",
                                   categories=categories,
                                   error="Invalid image. Use JPG, JPEG, PNG, or WebP.")
        

    db.execute("""
    INSERT INTO foods (
    category_id,
    name,
    description,
    price,
    image
    )
    VALUES (?, ?, ?, ?, ?)
    """, (
        category_id,
        name,
        description,
        price,
        image_filename
    ))

    db.commit()
    db.close()

    return redirect(url_for("admin_foods"))


@app.route("/admin/foods/<int:food_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_food(food_id):

    db = get_db()

    food = db.execute("""
    SELECT
    id,
    category_id,
    name,
    description,
    price,
    image,
    available
    FROM foods
    WHERE id = ?
    """, (food_id,)).fetchone()

    if food is None:
        db.close()
        return "Food not found.", 404

    categories = db.execute("""
    SELECT id, name
    FROM categories
    ORDER BY name
    """).fetchall()

    if request.method == "GET":
        db.close()

        return render_template(
            "admin_edit_food.html",
            food=food,
            categories=categories
        )

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    price = request.form.get("price", "").strip()
    image_file = request.files.get("image")
    category_id = request.form.get("category_id", "").strip()

    if not name or not price or not category_id:
        db.close()

        return render_template("admin_edit_food.html",
                               food=food,
                               categories=categories,
                               error="Name, price and category are required.")

    try:
        price = int(price)
        category_id = int(category_id)
    except ValueError:
        db.clsoe()

        return render_template("admin_edit_food.html",
                               food=food,
                               categories=categories,
                               error="Price and category must be valid numbers.")

    if price < 0:
        db.close()

        return render_template(
            "admin_edit_food.html",
            food=food,
            categories=categories,
            error="Price cannot be negative."
        )

    category = db.execute("""
    SELECT id
    FROM categories
    WHERE id = ?
    """, (category_id,)).fetchone()

    if category is None:
        db.close()

        return render_template("admin_edit_food.html",
                               food=food,
                               categories=categories,
                               error="Invalid category.")

    image_filename = food["image"]
    new_image_filename = None

    if image_file and image_file.filename:

        new_image_filename = save_uploaded_image(image_file)

        if new_image_filename is None:
            db.close()

            return render_template(
                "admin_edit_food.html",
                food=food,
                categories=categories,
                error="Invalid image. Use JPG, JPEG, PNG, or WebP"
            )

        image_filename = new_image_filename



    db.execute("""
    UPDATE foods
    SET
    category_id = ?,
    name = ?,
    description = ?,
    price = ?,
    image = ?
    WHERE id = ?
    """, (
        category_id,
        name,
        description,
        price,
        image_filename,
        food_id
    ))

    db.commit()

    if new_image_filename and food["image"]:

        old_image_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            food["image"]
        )

        if os.path.exists(old_image_path):
            os.remove(old_image_path)

    db.close()

    return redirect(url_for("admin_foods"))


@app.route("/admin/categories")
@admin_required
def admin_categories():

    db = get_db()

    categories = db.execute("""
    SELECT
    categories.id,
    categories.name,
    COUNT(foods.id) AS food_count
    FROM categories
    LEFT JOIN foods
    ON foods.category_id = categories.id
    GROUP BY categories.id
    ORDER BY categories.name
    """).fetchall()

    db.close()

    return render_template(
        "admin_categories.html",
        categories=categories
    )


@app.route("/admin/categories/add", methods=["GET", "POST"])
@admin_required
def admin_add_category():

    if request.method == "GET":
        return render_template('admin_add_category.html')

    name = request.form.get("name", "").strip()

    if not name:
        return render_template(
            "admin_add_category.html",
            error="Category name is required."
        )

    db = get_db()

    existing = db.execute("""
    SELECT id 
    FROM categories
    WHERE name = ?
    """, (name,)).fetchone()

    if existing is not None:
        db.close()

        return render_template(
            "admin_add_category.html",
            error="That category already exists."
        )

    db.execute("""
    INSERT INTO categories (name)
    VALUES (?)
    """, (name,))

    db.commit()
    db.close()

    return redirect(url_for('admin_categories'))


@app.route("/admin/logout")
def admin_logout():

    session.pop("admin_id", None)
    session.pop("admin_username", None)

    return redirect(url_for("admin_login"))


@app.route("/menu")
def menu():
    db = get_db()

    foods = db.execute("""
                       SELECT 
                       foods.id,
                       foods.name,
                       foods.description,
                       foods.price,
                       foods.image,
                       foods.category_id
                       FROM foods
                       WHERE foods.available = 1
                       """).fetchall()

    categories = db.execute("""
    SELECT id, name 
    FROM categories""").fetchall()
    
    db.close()

    return render_template("menu.html", foods=foods, categories=categories)

@app.route("/food/<int:id>")
def food(id):
    db = get_db()

    food = db.execute("""
        SELECT
            foods.id,
            foods.name,
            foods.description,
            foods.price,
            foods.image,
            foods.available,
            categories.name AS category_name
        FROM foods
        JOIN categories
            ON foods.category_id = categories.id
        WHERE foods.id = ? AND foods.available = 1
    """, (id,)).fetchone()

    db.close()

    if food is None:
        return "Food not found", 404
    
    return render_template("food.html", food=food)


@app.route("/cart/add/<int:id>", methods=["POST"])
def add_to_cart(id):

    db = get_db()

    food = db.execute("""
                      SELECT id
                      FROM foods
                      WHERE id = ? AND available = 1
                      """, (id,)).fetchone()
    
    db.close()

    if food is None:
        return "Food not found", 404
    
    cart = session.get("cart", {})

    food_id = str(id)

    if food_id in cart:
        cart[food_id] += 1
    else:
        cart[food_id] = 1

    session["cart"] = cart

    return redirect(url_for("cart"))


@app.route("/cart")
def cart():
    cart = session.get("cart", {})

    if not cart:
        return render_template("cart.html", foods=[], subtotal=0)
    
    db= get_db()

    food_ids = list(cart.keys())

    placeholders = ",".join("?" * len(food_ids))

    foods = db.execute(f"""
                       SELECT id, name, price, image, available
                       FROM foods
                       WHERE id IN ({placeholders})
                       """, food_ids).fetchall()
    
    db.close()

    subtotal = 0

    for food in foods:
        if food["available"]:
            quantity = cart[str(food["id"])]
            subtotal += food["price"] * quantity

    return render_template("cart.html",
                           foods=foods,
                           cart=cart,
                           subtotal=subtotal)


@app.route("/cart/update/<int:id>", methods=["POST"])
def update_cart(id):

    cart = session.get("cart", {})

    food_id = str(id)

    if food_id not in cart:
        return redirect(url_for("cart"))
    
    action = request.form.get("action")

    if action == "increase":
        cart[food_id] += 1

    elif action == "decrease":
        cart[food_id] -= 1

        if cart[food_id] <= 0:
            del cart[food_id]

    session["cart"] = cart

    return redirect(url_for("cart"))



@app.route("/cart/remove/<int:id>", methods=["POST"])
def remove_from_cart(id):

    cart = session.get("cart", {})

    food_id = str(id)

    cart.pop(food_id, None)

    session["cart"] = cart

    return redirect(url_for("cart"))


@app.route("/checkout", methods=["POST", "GET"])
def checkout():

    cart = session.get("cart", {})

    if not cart:
        return redirect(url_for("cart"))
    
    if request.method == "GET":

        db = get_db()

        food_ids = list(cart.keys())

        placeholder = ",".join("?" * len(food_ids))

        foods = db.execute(f"""
                        SELECT id, name, price, available
                        FROM foods
                        WHERE id IN ({placeholder})
                        """, food_ids).fetchall()
        
        db.close()

        unavailable = [
            food for food in foods
            if not food["available"]
        ]

        if unavailable:
            return render_template(
                "cart.html",
                foods=foods,
                cart=cart,
                unavailable=unavailable,
                subtotal=0
            )
        
        subtotal = 0

        for food in foods:
            quantity = cart[str(food["id"])]
            subtotal += food["price"] * quantity

        delivery_fee = 2000

        total = subtotal + delivery_fee

        return render_template("checkout.html",
                            foods=foods,
                            cart=cart,
                            subtotal=subtotal,
                            delivery_fee=delivery_fee,
                            total=total)
    
    customer_name = request.form.get("customer_name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address","").strip()

    if not customer_name or not phone or not address:
        return "Please fill in all fields.", 400
    
    db = get_db()

    try:

        food_ids = list(cart.keys())
        placeholders = ",".join("?" * len(food_ids))

        foods = db.execute(f"""
                           SELECT id, name, price, available
                           FROM foods
                           WHERE id IN ({placeholders})
                           """, food_ids).fetchall()
        
        if len(foods) != len(food_ids):
            return "One or more foods no longer exist.", 400
        

        for food in foods:
            if not food["available"]:
                return f"{food['name']} is currently unavailable.", 400
            
        subtotal = 0

        for food in foods:
            quantity = cart[str(food["id"])]
            subtotal += food["price"] * quantity

        delivery_fee = 2000
        total = subtotal + delivery_fee

        import secrets

        tracking_code = secrets.token_hex(4).upper()

        cursor = db.execute("""
                            INSERT INTO orders (
                            tracking_code,
                            customer_name,
                            phone,
                            address,
                            subtotal,
                            delivery_fee,
                            total)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (
                                tracking_code,
                                customer_name,
                                phone,
                                address,
                                subtotal,
                                delivery_fee,
                                total
                            ))
        
        order_id = cursor.lastrowid

        for food in foods:
            
            quantity = cart[str(food["id"])]

            db.execute("""
                       INSERT INTO order_items (
                       order_id,
                       food_id,
                       quantity,
                       price)
                       VALUES (?, ?, ?, ?)
                       """, (
                           order_id,
                           food["id"],
                           quantity,
                           food["price"]
                       ))
            
        db.commit()

    except Exception:

        db.rollback()

        return "Something went wrong while creating your order,", 500
    
    finally:
        db.close()

    session.pop("cart", None)

    return redirect(
        url_for(
            "payment",
            tracking_code=tracking_code
        )
    )


@app.route("/order-created/<tracking_code>")
def order_created(tracking_code):

    db = get_db()

    order = db.execute("""
    SELECT
    id,
    tracking_code,
    status,
    payment_status
    FROM orders
    WHERE tracking_code = ?
    """, (tracking_code,)).fetchone()

    db.close()

    if order is None:
        return "Order not found.", 404

    if order["payment_status"] != "paid":
        return redirect(
            url_for(
                "payment",
                tracking_code=tracking_code
            )
        )
    return render_template(
        "order_created.html",
        tracking_code=tracking_code
    )


@app.route("/track", methods=["GET", "POST"])
def track():

    if request.method == "GET":
        return render_template("track.html")
    
    tracking_code = request.form.get("tracking_code", "".strip().upper())


    if not tracking_code:
        return render_template(
            "track.html",
            error="Please enter your tracking code."
        )
    
    db = get_db()

    order = db.execute("""
                       SELECT 
                       id,
                       tracking_code,
                       customer_name,
                       total,
                       status,
                       payment_status,
                       created_at
                       FROM orders
                       WHERE tracking_code = ?
                       """, (tracking_code,)).fetchone()
    if order is None:
        db.close()

        return render_template(
            "track.html",
            error="Order not found."
        )
    
    items = db.execute("""
                       SELECT 
                       order_items.quantity,
                       order_items.price,
                       foods.name
                       FROM order_items
                       JOIN foods
                       ON order_items.food_id = foods.id
                       WHERE order_items.order_id = ?
                       """, (order["id"],)).fetchall()
    
    db.close()

    return render_template(
        "track.html",
        order=order,
        items=items
    )


@app.route("/payment/<tracking_code>/simulate-success", methods=["POST"])
def simulate_payment(tracking_code):

    db = get_db()

    try:
        order = db.execute("""
        SELECT id, tracking_code, status, payment_status
        FROM orders
        WHERE tracking_code = ?
        """, (tracking_code,)).fetchone()

        if order is None:
            return "Order not found", 404

        if order["payment_status"] == "paid":
            return redirect(
                url_for(
                    "order_created",
                    tracking_code=tracking_code
                )
            )

        if order["payment_status"] != "pending":
            return "This order cannot be paid", 400

        import secrets

        test_payment_id = "TEST-" + secrets.token_hex(8).upper()

        db.execute("""
        UPDATE orders
        SET
        payment_status = 'paid',
        payment_id = ?,
        status = 'confirmed'
        WHERE tracking_code = ?
        """,(
            test_payment_id,
            tracking_code
        ))

        db.commit()

    except Exception:
        db.rollback()
        return "Payment simulation failed", 500

    finally:
        db.close()

    return redirect(
        url_for(
            "order_created",
            tracking_code=tracking_code
        )
    )


@app.route("/payment/<tracking_code>")
def payment(tracking_code):

    db = get_db()

    order = db.execute("""
    SELECT
    id,
    tracking_code,
    total,
    status,
    payment_status
    FROM orders 
    WHERE tracking_code =?
    """,(tracking_code,)).fetchone()

    db.close()

    if order is None:
        return "Order not found.", 404

    if order["payment_status"] == "paid":
        return redirect(
            url_for(
                "order_created",
                tracking_code=tracking_code
            )
        )

    return render_template(
        "payment.html",order=order
    )