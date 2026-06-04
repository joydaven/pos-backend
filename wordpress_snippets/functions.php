<?php
// wp rocket cache 
add_filter( 'rocket_is_user_logged_in', '__return_true' );

add_action('wp_footer', function () {
    ?>
    <script>
    jQuery(document).on('shown.bs.modal', function () {
        jQuery('.modal-backdrop.fade.show').remove();
    });
    </script>
    <?php
});

function dt_enqueue_styles() {
    $parenthandle = 'divi-style'; // Handle for parent theme
    $theme = wp_get_theme();

    // Enqueue parent theme style
    wp_enqueue_style($parenthandle, get_template_directory_uri() . '/style.css',
        array(),
        $theme->parent()->get('Version')
    );

    // Enqueue child theme style
    wp_enqueue_style('child-style', get_stylesheet_uri(),
        array($parenthandle),
        $theme->get('Version')
    );
}

// For normal front-end pages
add_action('wp_enqueue_scripts', 'dt_enqueue_styles');
// For wp-signup.php and multisite-related screens
add_action('signup_header', 'dt_enqueue_styles');

/**
 * Add styling to the WordPress multisite signup page and fix logo display
 */
function doctors_studio_style_signup_page() {
    // Only apply to wp-signup.php
    if (strpos($_SERVER['REQUEST_URI'], 'wp-signup.php') !== false) {
        // Add styling
        ?>
        <style>
            /* Hide navigation elements */
            #main-header, 
            #et-top-navigation,
            #mega-menu-wrap-primary,
            #mega-menu-primary {
                display: none !important;
            }
            
            /* Remove any blue bars */
            *[style*="background:#1A3C5A"],
            *[style*="background-color:#1A3C5A"],
            *[style*="background: #1A3C5A"],
            div[style*="height:40px"],
            .signup-logo-container:after,
            .signup-logo-container:before,
            #signup-content:before {
                display: none !important;
                height: 0 !important;
                background: none !important;
            }
            
            /* Keep only one logo */
            .ds-logo-container:not(:first-of-type) {
                display: none !important;
            }
            
            /* Style the heading with space below */
            h2, #signup-welcome {
                color: #333;
                font-size: 24px;
                font-weight: 600;
                margin-bottom: 70px !important;
                padding: 30px 0 !important;
                text-align: center;
                font-family: "Open Sans", Arial, sans-serif;
                background-color: #e8f4f8 !important;
            }
            
            /* Style the form */
            #setupform {
                max-width: 800px;
                margin: 60px auto 20px;
                padding: 30px;
                background-color: #fff;
                border-radius: 5px;
                box-shadow: 0 2px 15px rgba(0,0,0,0.1);
                font-family: "Open Sans", Arial, sans-serif;
            }
            
            #setupform input[type="text"],
            #setupform input[type="email"],
            #setupform input[type="password"],
            #username,
            #email {
                width: 100%;
                padding: 12px 15px;
                margin-bottom: 15px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 16px;
                color: #333;
                background-color: #fff;
                box-shadow: none;
                box-sizing: border-box;
            }
            
            #setupform input[type="submit"],
            input#submit,
            .submit .button {
                background-color: #1e73be;
                color: white;
                padding: 12px 20px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                width: 100%;
                font-size: 16px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            
            #setupform label {
                display: block;
                margin-bottom: 8px;
                font-weight: 600;
                color: #333;
            }
            
            p.message {
                text-align: center;
                margin-bottom: 20px;
            }
        </style>
        
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Check if our logo already exists
            var existingLogos = document.querySelectorAll('.ds-logo-container');
            if (existingLogos.length === 0) {
                // Create logo element with link to homepage
                var logoContainer = document.createElement('div');
                logoContainer.className = 'ds-logo-container';
                logoContainer.style.textAlign = 'center';
                logoContainer.style.padding = '20px 0';
                logoContainer.style.backgroundColor = '#fff';
                
                var logoLink = document.createElement('a');
                logoLink.href = 'https://store.doctorsstudio.com/';
                logoLink.title = 'Return to Doctors Studio Homepage';
                
                var logo = document.createElement('img');
                logo.src = 'https://store.doctorsstudio.com/wp-content/uploads/2024/06/Doctors-Studio-Logo.png';
                logo.alt = 'Doctors Studio Logo';
                logo.className = 'ds-logo';
                logo.style.maxWidth = '250px';
                
                // Assemble and insert at the top of the body
                logoLink.appendChild(logo);
                logoContainer.appendChild(logoLink);
                document.body.insertBefore(logoContainer, document.body.firstChild);
            } else {
                // If we have multiple logos, keep only the first one
                for (var i = 1; i < existingLogos.length; i++) {
                    existingLogos[i].style.display = 'none';
                }
            }
            
            // Add colored background to heading
            var heading = document.querySelector('h2');
            if (heading) {
                heading.style.backgroundColor = '#e8f4f8';
                heading.style.padding = '30px 0 70px';
                heading.style.marginBottom = '50px';
            }
        });
        </script>
        <?php
    }
}
// Hook into both signup_header and wp_head to ensure it works
add_action('signup_header', 'doctors_studio_style_signup_page', 999);
add_action('wp_head', 'doctors_studio_style_signup_page', 999);

function my_theme_enqueue_styles() {
    wp_enqueue_style('parent-style', get_template_directory_uri() . '/style.css');
    wp_enqueue_style('child-style', get_stylesheet_directory_uri() . '/style.css', array('parent-style'));
}
add_action('wp_enqueue_scripts', 'my_theme_enqueue_styles');



function replace_wp_login_with_studio_login() {
    // Check if the current request URL contains 'wp-login.php'
    if (strpos($_SERVER['REQUEST_URI'], 'wp-login.php') !== false) {
        // Build the new URL
        $new_url = site_url('studio-login.php');
        
        // If there's a query string, append it to the new URL
        if (!empty($_SERVER['QUERY_STRING'])) {
            $new_url .= '?' . $_SERVER['QUERY_STRING'];
        }
        
        // Redirect to the new URL
        wp_redirect($new_url);
        exit;
    }
}

// User Login change url
// Use the template_redirect hook for redirection
add_action('template_redirect', 'replace_wp_login_with_studio_login');


add_filter('wp_nav_menu_objects', 'customize_login_logout_menu', 10, 2);

function customize_login_logout_menu($items, $args) {
    // Check if the user is logged in
    $logged_in = is_user_logged_in();

    // Loop through each menu item
    foreach ($items as $item) {
        // Check if the menu item is the login link
        if ($item->url === 'https://store.doctorsstudio.com/studiologin/') {
            if ($logged_in) {
                // Change the menu item to "Logout" if the user is logged in
                $item->title = 'Logout';
                $item->url = wp_logout_url(home_url());
            } else {
                // Ensure it remains "Login" if the user is not logged in
                $item->title = 'Login';
                $item->url = 'https://store.doctorsstudio.com/studiologin/';
            }
        }
    }
    
    return $items;
}


// css for deliver
function add_custom_css_with_js() {
    echo '
    <script>
    document.addEventListener("DOMContentLoaded", function() {
        var style = document.createElement("style");
        style.innerHTML = ".et_pb_wc_add_to_cart_0_tb_body select { color: black !important; }";
        document.head.appendChild(style);
    });
    </script>
    ';
}

// Hook the function to wp_footer action to ensure it runs after all content is loaded
add_action('wp_footer', 'add_custom_css_with_js', 100);

//jdn custom font
/*function custom_css_enqueue_styles() {
    wp_enqueue_style(
        'custom_css', // Handle name
        get_stylesheet_directory_uri() . '/customstyle.css', 
        array(), // Dependencies
        null, // Version number
        'all' // Media type
    );
}
add_action('wp_enqueue_scripts', 'custom_css_enqueue_styles');
*/

//Design for Health Taxonomy

// function enqueue_custom_scripts() {
//     // Deregister the default jQuery included with WordPress
//     wp_deregister_script('jquery');

//     // Register jQuery
//     wp_register_script('jquery', includes_url('/js/jquery/jquery.js'), false, NULL, true);
//     wp_enqueue_script('jquery');

//     // Ensure Bootstrap JS is correctly enqueued, if required. Adjust the path if necessary.
//     $bootstrap_js_path = get_template_directory_uri() . '/js/bootstrap.min.js';
//     if (file_exists(get_template_directory() . '/js/bootstrap.min.js')) {
//         wp_register_script('bootstrap', $bootstrap_js_path, array('jquery'), '1.0', true);
//         wp_enqueue_script('bootstrap');
//     }

//     // Custom JavaScript
//     $custom_js = "
//     document.addEventListener('DOMContentLoaded', function() {
//         var buttonTextGuest = 'LOGIN/REGISTER';
//         var buttonTextMember = 'MEMBER ONLY';
//         var loginLink = 'https://store.doctorsstudio.com/studiologin/';

//         // Function to update buttons
//         function updateButtons() {
//             var buttons = document.querySelectorAll('.product a.button');
//             buttons.forEach(function(buttonElement) {
//                 var productElement = buttonElement.closest('.product');
//                 if (productElement) {
//                     var terms = productElement.querySelector('.woocommerce-loop-product__categories');
//                     if (terms && terms.textContent.includes('Designs for Health')) {
//                         if (!isLoggedIn) {
//                             buttonElement.innerText = buttonTextGuest;
//                             buttonElement.href = loginLink;
//                             buttonElement.addEventListener('click', function(event) {
//                                 event.preventDefault();
//                                 window.location.href = loginLink;
//                             });
//                             // Hide the price for guests
//                             var priceElement = productElement.querySelector('.price');
//                             if (priceElement) {
//                                 priceElement.style.display = 'none';
//                             }
//                         } else {
//                             buttonElement.innerText = buttonTextMember;
//                             buttonElement.href = loginLink;
//                             buttonElement.addEventListener('click', function(event) {
//                                 event.preventDefault();
//                                 window.location.href = loginLink;
//                             });
//                         }
//                     }
//                 }
//             });
//         }

//         // Check if the URL contains specific query parameters
//         if (window.location.search.includes('filter=true') && window.location.search.includes('pa_brand=designs-for-health-brand')) {
//             // Check if the user is logged in
//             var isLoggedIn = " . (is_user_logged_in() ? 'true' : 'false') . ";

//             // Initially update buttons
//             updateButtons();

//             // Observe for any changes in the product listing
//             var observer = new MutationObserver(updateButtons);
//             observer.observe(document.body, { childList: true, subtree: true });

//             // Re-run the updateButtons function at intervals to ensure it catches all elements
//             setInterval(updateButtons, 1000);
//         }
//     });
//     ";
//     wp_add_inline_script('jquery', $custom_js);
// }
// add_action('wp_enqueue_scripts', 'enqueue_custom_scripts');

// function register_custom_menus() {
//     register_nav_menus(array(
//         'primary' => __('Primary Menu', 'theme-text-domain'),
//         'footer' => __('Footer Menu', 'theme-text-domain')
//     ));
// }
// add_action('after_setup_theme', 'register_custom_menus');

// function theme_setup() {
//     add_theme_support('title-tag');
//     add_theme_support('post-thumbnails');
//     add_theme_support('html5', array('search-form', 'comment-form', 'comment-list', 'gallery'));
// }
// add_action('after_setup_theme', 'theme_setup');


// function enqueue_custom_scripts() {
//     wp_enqueue_script('jquery');

//     $custom_js = "
//     document.addEventListener('DOMContentLoaded', function() {
//         var buttonTextGuest = 'LOGIN/REGISTER';
//         var buttonTextMember = 'MEMBER ONLY';
//         var loginLink = 'https://store.doctorsstudio.com/studiologin/';

//         // Check if the user is logged in
//         var isLoggedIn = " . (is_user_logged_in() ? 'true' : 'false') . ";

//         var buttons = document.querySelectorAll('.product a.button');
//         buttons.forEach(function(buttonElement) {
//             var productElement = buttonElement.closest('.product');
//             if (productElement && productElement.classList.contains('category-designs-for-health')) {
//                 if (!isLoggedIn) {
//                     buttonElement.innerText = buttonTextGuest;
//                     buttonElement.href = loginLink;
//                     buttonElement.addEventListener('click', function(event) {
//                         event.preventDefault();
//                         window.location.href = loginLink;
//                     });
//                     // Hide the price for guests
//                     var priceElement = productElement.querySelector('.price');
//                     if (priceElement) {
//                         priceElement.style.display = 'none';
//                     }
//                 } else {
//                     buttonElement.innerText = buttonTextMember;
//                     buttonElement.href = loginLink;
//                     buttonElement.addEventListener('click', function(event) {
//                         event.preventDefault();
//                         window.location.href = loginLink;
//                     });
//                 }
//             }
//         });
//     });
//     ";
//     wp_add_inline_script('jquery', $custom_js);
// }
// add_action('wp_enqueue_scripts', 'enqueue_custom_scripts');

// function register_custom_menus() {
//     register_nav_menus(array(
//         'primary' => __('Primary Menu', 'theme-text-domain'),
//         'footer' => __('Footer Menu', 'theme-text-domain')
//     ));
// }
// add_action('after_setup_theme', 'register_custom_menus');

// function theme_setup() {
//     add_theme_support('title-tag');
//     add_theme_support('post-thumbnails');
//     add_theme_support('html5', array('search-form', 'comment-form', 'comment-list', 'gallery'));
// }
// add_action('after_setup_theme', 'theme_setup');

// add_filter('woocommerce_get_price_html', 'hide_price_for_guests', 10, 2);
// function hide_price_for_guests($price, $product) {
//     if (!is_user_logged_in() && has_term('designs-for-health', 'product_cat', $product->get_id())) {
//         return '';
//     }
//     return $price;
// }

// // Modify the add to cart button for products in "Designs for Health" category
// add_filter('woocommerce_loop_add_to_cart_link', 'modify_add_to_cart_button', 10, 2);
// function modify_add_to_cart_button($button, $product) {
//     $product_id = $product->get_id();

//     // Check if the product is in the "Designs for Health" category
//     if (has_term('designs-for-health', 'product_cat', $product_id)) {
//         $buttonTextGuest = 'LOGIN/REGISTER';
//         $buttonTextMember = 'MEMBER ONLY';
//         $loginLink = 'https://store.doctorsstudio.com/studiologin/';

//         if (!is_user_logged_in()) {
//             $button = '<a class="button designs-for-health" href="' . $loginLink . '" data-product_id="' . $product_id . '">' . $buttonTextGuest . '</a>';
//         } else {
//             $button = '<a class="button designs-for-health" href="' . $loginLink . '" data-product_id="' . $product_id . '">' . $buttonTextMember . '</a>';
//         }
//     }

//     return $button;
// }


// add_filter('woocommerce_get_price_html', 'hide_price_for_guests', 10, 2);
// function hide_price_for_guests($price, $product) {
//     if (!is_user_logged_in() && has_term('designs-for-health', 'pa_brand', $product->get_id())) {
//         return '';
//     }
//     return $price;
// }

// add_filter('woocommerce_loop_add_to_cart_link', 'modify_add_to_cart_button', 10, 2);
// function modify_add_to_cart_button($button, $product) {
//     $product_id = $product->get_id();

//     // Check if the product is in the "Designs for Health" taxonomy
//     if (has_term('designs-for-health', 'pa_brand', $product_id)) {
//         $buttonTextGuest = 'LOGIN/REGISTER';
//         $buttonTextMember = 'MEMBER ONLY';
//         $loginLink = 'https://store.doctorsstudio.com/studiologin/';

//         if (!is_user_logged_in()) {
//             $button = '<a class="button designs-for-health" href="' . $loginLink . '" data-product_id="' . $product_id . '">' . $buttonTextGuest . '</a>';
//         } else {
//             $button = '<a class="button designs-for-health" href="' . $loginLink . '" data-product_id="' . $product_id . '">' . $buttonTextMember . '</a>';
//         }
//     }

//     return $button;
// }

//Woo Notice 



function enqueue_custom_scripts() {
    wp_enqueue_script('jquery');

    $custom_js = "
    function checkForButton() {
        var productId = 4548;
        var buttonText = 'LOGIN/REGISTER';
        var newLink = 'https://store.doctorsstudio.com/studiologin/';

        var buttonElement = document.querySelector('a[data-product_id=\"' + productId + '\"]');
        if (buttonElement) {
            buttonElement.innerText = buttonText;
            buttonElement.href = newLink;
            buttonElement.addEventListener('click', function(event) {
                event.preventDefault();
                window.location.href = newLink;
            });
            clearInterval(checkInterval);
        }
    }

    var checkInterval = setInterval(checkForButton, 1000);
    ";

    wp_add_inline_script('jquery', $custom_js);
}
add_action('wp_enqueue_scripts', 'enqueue_custom_scripts');

function register_custom_menus() {
    register_nav_menus(array(
        'primary' => __('Primary Menu', 'theme-text-domain'),
        'footer' => __('Footer Menu', 'theme-text-domain')
    ));
}
add_action('after_setup_theme', 'register_custom_menus');

function theme_setup() {
    add_theme_support('title-tag');
    add_theme_support('post-thumbnails');
    add_theme_support('html5', array('search-form', 'comment-form', 'comment-list', 'gallery'));
}
add_action('after_setup_theme', 'theme_setup');


function custom_divi_modules() {
    if (function_exists('WC')) {
        include_once get_stylesheet_directory() . '/includes/builder/module/woocommerce/class-et-builder-module-woocommerce-cart-notice.php';
    }
}
add_action('et_builder_ready', 'custom_divi_modules');


// My account menu item 
function add_login_status_script() {
    if (!is_user_logged_in()) {
        ?>
        <script type="text/javascript">
            document.addEventListener('DOMContentLoaded', function() {
                var myAccountMenuItem = document.querySelector('.my-account');
                if (myAccountMenuItem) {
                    myAccountMenuItem.style.display = 'none';
                }
            });
        </script>
        <?php
    }
}
add_action('wp_footer', 'add_login_status_script');
// 

// No index products
function add_noindex_to_product_pages() {
    if (function_exists('is_product') && is_product()) {
        echo '<meta name="robots" content="noindex">';
    }
}
add_action('wp_head', 'add_noindex_to_product_pages');
// 

// Password Protect 

// Check the password on page load
function enqueue_custom_script() {
    if (is_page('pos2') && !current_user_can('administrator')) {
        wp_enqueue_script('custom-password-script', get_stylesheet_directory_uri() . '/js/custom-password.js', array('jquery'), null, true);
    }
}
add_action('wp_enqueue_scripts', 'enqueue_custom_script');
// Function to restrict access to pos2 page and redirect to 404 if not logged in
function restrict_pos2_page() {
    if (is_page('pos2') && !is_user_logged_in()) {
        // Redirect to custom 404 page if not logged in
        wp_redirect('https://store.doctorsstudio.com/404');
        exit();
    }
}
add_action('template_redirect', 'restrict_pos2_page');

// woo notice

// Add Notices Wrapper in Template
function add_woocommerce_notices_wrapper() {
    ?>
    <div class="woocommerce-notices-wrapper">
        <?php wc_print_notices(); ?>
        <a href="<?php echo esc_url(wc_get_cart_url()); ?>" class="button wc-forward" id="ViewCart">View Cart</a>
    </div>
    <script type="text/javascript">
        jQuery(function($) {
            // Ensure the event listener is attached even after a page reload
            function addCartListener() {
                $(document.body).on('added_to_cart', function() {
                    console.log('Product added to cart, reloading page...');
                    location.reload();
                });
            }

            // Initial listener attachment
            addCartListener();

            // Reattach the listener after any AJAX event
            $(document.body).on('wc_fragments_loaded wc_fragments_refreshed', function() {
                addCartListener();
            });
        });
    </script>
    <?php
}
add_action( 'woocommerce_before_main_content', 'add_woocommerce_notices_wrapper', 5 );


// Add custom notice when a product is added to the cart
function my_add_to_cart_message( $cart_item_key, $product_id ) {
    if ( is_page(4535) ) { // Replace with your actual page slug
        wc_clear_notices(); // Clear any previous notices
        $product = wc_get_product( $product_id );
        
        // Construct the message with the View Cart button
        $message = sprintf( __( '"%s" has been added to your cart.', 'woocommerce' ), $product->get_name() );
        $message .= ' <a href="' . esc_url( wc_get_cart_url() ) . '" class="button wc-forward">' . __( 'View cart', 'woocommerce' ) . '</a>';
        
        wc_add_notice( $message, 'success' );
    }
}
add_action( 'woocommerce_add_to_cart', 'my_add_to_cart_message', 10, 2 );

//custom payment jdn
add_filter('woocommerce_payment_gateways', 'care_credit_custom');
function care_credit_custom($gateways) {
    $gateways[] = 'WC_Gateway_carecredit';
    return $gateways;
}

add_filter('op_login_format_payment_data',function($payment_method_data,$methods){
    if($payment_method_data['code'] == 'carecredit')
    {
        $payment_method_data['type'] = 'offline';
        $payment_method_data['online_type'] = 'external';
        $payment_method_data['allow_refund'] = 'offline';
        $payment_method_data['partial'] = true;
        $payment_method_data['partial_type'] = 'offline';
    }
    //print_r($payment_method_data);echo 'movave';
    if($payment_method_data['code'] == 'customer_point')
    {
        $payment_method_data['name'] = 'Points';
    }
    if($payment_method_data['code'] == 'carecredit')
    {
        $payment_method_data['name'] = 'Care Credit';
    }
    return $payment_method_data;
},40,2);


    function authorize_op_addition_payment_methods2($payment_options){


        $payment_options['carecredit'] = array(
            'code' => 'carecredit',
            'admin_title' => __('care credit','openpos'),
            'frontend_title' => __('care credit','openpos'),
            'description' => ''
        );

        return $payment_options;
    }
add_filter('op_addition_payment_methods','authorize_op_addition_payment_methods2',10,1);



//user meta data checker  https://store.doctorsstudio.com/?display_user_meta=true&user_id=8925
// function display_user_meta_data() {
//     // Check if the user ID is provided in the URL
//     if (!isset($_GET['user_id']) || !is_numeric($_GET['user_id'])) {
//         wp_die('Invalid user ID');
//     }

//     $user_id = intval($_GET['user_id']);
    
//     // Retrieve all user meta data
//     $user_meta = get_user_meta($user_id);
    
//     if (empty($user_meta)) {
//         wp_die('No meta data found for this user');
//     }
    
//     // Display the user meta data in a readable format
//     echo '<pre>' . print_r($user_meta, true) . '</pre>';
    
//     // Stop further execution
//     exit;
// }

// function custom_user_meta_endpoint() {
//     // Check for a specific query var (e.g., `display_user_meta`)
//     if (isset($_GET['display_user_meta']) && $_GET['display_user_meta'] == 'true') {
//         display_user_meta_data();
//     }
// }
// add_action('template_redirect', 'custom_user_meta_endpoint');
// end of meta data checker per user


// function load_signup_styles() {
//     // Only enqueue styles on the wp-signup.php page
//     if ( strpos( $_SERVER['REQUEST_URI'], 'wp-signup.php' ) !== false ) {
//         wp_enqueue_style( 'signup-styles', get_template_directory_uri() . '/signup-style.css', array(), '1.0.0' );
//     }
// }
// add_action( 'wp_enqueue_scripts', 'load_signup_styles' );


function display_product_info() {
    // Check if the product ID is provided in the URL
    if (!isset($_GET['product_id']) || !is_numeric($_GET['product_id'])) {
        wp_die('Invalid product ID');
    }

    $product_id = intval($_GET['product_id']);
    
    // Retrieve the product short description
    $product_short_description = get_post_field('post_excerpt', $product_id);

    // Retrieve the brand name (assuming it's stored as a product attribute 'pa_brand')
    $brand = wc_get_product_terms($product_id, 'pa_brand', array('fields' => 'names'));
    $brand_name = isset($brand[0]) ? $brand[0] : 'No brand specified';

    // Display the brand name
    echo '<h3>Brand Name</h3>';
    echo '<p>' . esc_html($brand_name) . '</p>';

    // Display the short description
    echo '<h3>Short Description</h3>';
    echo '<p>' . esc_html($product_short_description ? $product_short_description : 'No short description available') . '</p>';

    // Stop further execution
    exit;
}

function custom_product_info_endpoint() {
    // Check for a specific query var (e.g., `display_product_info`)
    if (isset($_GET['display_product_info']) && $_GET['display_product_info'] == 'true') {
        display_product_info();
    }
}
add_action('template_redirect', 'custom_product_info_endpoint');

add_filter('http_request_args', function($args, $url) {
    if (strpos($url, 'https://forms.doctorsstudio.com') !== false) { // Match your IP or webhook URL
        $args['sslverify'] = false;
    }
    return $args;
}, 10, 2);
//product subscription jdn
add_filter('op_product_data', function ($product_data, $product_post) {
    $producto=wc_get_product($product_data['id']);
    if($producto->is_type('variable-subscription')){
        $product_data=subscription_product_attributesoption($product_data);
    }
    if($producto->is_type('subscription')){
        $product_data=simples_subscription_attributesoption($product_data);
    }
    return $product_data;
}, 210, 2);

//simple subscription jdn
function simples_subscription_attributesoption($product){
    $producto = wc_get_product($product['id']);
    $attributes = $producto->get_attributes();

    $trial_length = $producto->get_meta('_subscription_trial_length');
    $trial_period = $producto->get_meta('_subscription_trial_period');
    $subscription_period = $producto->get_meta('_subscription_period'); // 'day', 'week', 'month', or 'year'
    $subscription_interval = $producto->get_meta('_subscription_period_interval'); // Interval 'every',every2nd every6th

    $labelinterval=[1=>'',2=>'2nd',3=>'3rd',4=>'4th',5=>'5th',6=>'6th'];
    $sign_up_fee = $producto->get_meta('_subscription_sign_up_fee'); // Sign-up fee (if any)
    $value_id=$subscription_interval.'_'.$subscription_period;
    $term=['month'=>1,'year'=>12,'day'=>1];
    $termval=['Monthly'=>'1_month','Yearly'=>'1_year','6 Months'=>'6_month'];

    $termcost=['Monthly'=>0,'Yearly'=>-1*($product['price']*5),'6 Months'=>-1*($product['price']*2.5)];
    $opt[0]=[
        'value_id' => $value_id,
        'label' => $subscription_period.' for '. number_format(($product['price']*(isset($term[$subscription_period])?$term[$subscription_period]:1)),2),
        'cost'=>$termcost[$subscription_period],
        'cost_type' => 'discount_fixed'
    ];

    $product['options'][]=[
        'label'=>'Subscribtion',
        'option_id'=>'convert_to_sub',
        'type'=>'radio',
        'require'=>0,
        'default' => array($value_id),
        'options'=>$opt,
    ];
    return $product;
}
function subscription_product_attributesoption($product) {
    $producto = wc_get_product($product['id']);
    $attributes = $producto->get_attributes();
    $prodsubscriptionjdn=getAttributeDetail($attributes,$product['id']);
    $product=simpleSubscriptionCreateOptions($product,$prodsubscriptionjdn,$product['id']);
    return $product;
}
//product subscription jdn
function simpleSubscriptionCreateOptions($product,$option,$product_id){
    $producto = wc_get_product($product_id);
    $trial_length = $producto->get_meta('_subscription_trial_length');
    $trial_period = $producto->get_meta('_subscription_trial_period');
    $trialc=0;

    $term=['Monthly'=>1,'Yearly'=>12,'6 Months'=>6];
    $termval=['Monthly'=>'1_month','Yearly'=>'1_year','6 Months'=>'6_month'];

    $termcost=['Monthly'=>0,'Yearly'=>-1*($product['price']*5),'6 Months'=>-1*($product['price']*2.5)];
    foreach($option as $k=>$op){
        $opt=[];
        if($k=='Billing Period'){
            foreach($op as $o){
                if($trial_length==30 && $trial_period=='days' && $o=='Monthly'){
                    $trialc=-1*($product['price']);
                }
                if($trial_length==1 && $trial_period=='months'){
                    $trialc=-1*($product['price']);
                }
                $opt['options'][]=[
                    'value_id' => (isset($termval[$o])?$termval[$o]:0),
                    'label' => $o.' for '. number_format(($product['price']*(isset($term[$o])?$term[$o]:1)),2),
                    'cost'=>$termcost[$o],//+$trialc,
                    'cost_type' => 'discount_fixed'
                ];
            }
            $product['options'][]=[
                'label'=>'Subscribe and get a discount',
                'option_id'=>'convert_to_sub',
                'type'=>'radio',
                'require'=>1,
                'default'=>null,
                'options'=>$opt['options'],
            ];
        }
    }
    return $product;
}

//product subscription jdn
function getAttributeDetail($attributes,$product_id){
    $attr=[];
    foreach ( $attributes as $attribute_name => $attribute ) {
        if ( $attribute->is_taxonomy() ) {
            $terms = wp_get_post_terms( $product_id, $attribute_name );
            $tr=[];
            foreach($terms as $trm){
                $tr[$trm->term_id]=$trm->name;
            }
            $attr[wc_attribute_label( $attribute_name )]=$tr;
        } else {
            $options = $attribute->get_options();
            $attr[wc_attribute_label( $attribute_name )]=$options;
        }
    }
    return $attr;
}

//product price on front end
function enqueue_dynamic_price_script() {
    if (function_exists('is_product') && is_product()) { // Only run on WooCommerce product pages
        global $post;

        // Ensure we have the product ID
        $product_id = $post->ID ?? 0;
        if (!$product_id) return;

        // Get the product object
        $product = wc_get_product($product_id);
        if (!$product) return;

        // Fetch the correct price and currency symbol
        $product_price = wc_get_price_to_display($product);
        $currency_symbol = html_entity_decode(get_woocommerce_currency_symbol()); // Decode symbol

        // Pass the price to JavaScript
        $product_data = array(
            'price' => $product_price,
            'currencySymbol' => $currency_symbol
        );

        wp_add_inline_script('jquery', 'var productData = ' . json_encode($product_data) . ';');
    }
}
add_action('wp_enqueue_scripts', 'enqueue_dynamic_price_script');


//Shipping cost rate
add_filter('woocommerce_package_rates', 'modify_flat_rate_shipping', 10, 2);

function modify_flat_rate_shipping($rates, $package) {
    $cart_total = WC()->cart->subtotal;

    foreach ($rates as $rate_id => $rate) {
        if ($rate_id === 'flat_rate:5') {
            $rates[$rate_id]->cost = ($cart_total >= 150) ? 0 : 9.99;
        }
    }

    return $rates;
}

// 
add_action('rest_api_init', function () {
    register_rest_route('custom/v1', '/get-user-data', [
        'methods' => 'GET',
        'callback' => 'get_current_user_data',
        'permission_callback' => '__return_true', // Allow all requests
    ]);
});

function restrict_to_laravel(WP_REST_Request $request) {
    $auth_token = $request->get_header('X-Custom-Token');
    $valid_token = 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdXRoQ2xhc3MiOiJMb2NhdGlvbiIsImF1dGhDbGFzc0lkIjoicGdmZWtsNnNLZ29mVlBTdVlPSm8iLCJzb3VyY2UiOiJJTlRFR1JBVElPTiIsInNvdXJjZUlkIjoiNjVmODQ5ZjAyYjlmNjgwMDM5MmQzYTM2LWx0eDBxZ2RwIiwiY2hhbm5lbCI6Ik9BVVRIIiwicHJpbWFyeUF1dGhDbGFzc0lkIjoicGdmZWtsNnNLZ29mVlBTdVlPSm8iLCJvYXV0aE1ldGEiOnsic2NvcGVzIjpbImJ1c2luZXNzZXMucmVhZG9ubHkiLCJidXNpbmVzc2VzLndyaXRlIiwiY29tcGFuaWVzLnJlYWRvbmx5IiwiY2FsZW5kYXJzLnJlYWRvbmx5IiwiY2FsZW5kYXJzLndyaXRlIiwiY2FsZW5kYXJzL2V2ZW50cy5yZWFkb25seSIsImNhbGVuZGFycy9ldmVudHMud3JpdGUiLCJjYWxlbmRhcnMvZ3JvdXBzLnJlYWRvbmx5IiwiY2FsZW5kYXJzL2dyb3Vwcy53cml0ZSIsImNhbGVuZGFycy9yZXNvdXJjZXMucmVhZG9ubHkiLCJjYWxlbmRhcnMvcmVzb3VyY2VzLndyaXRlIiwiY2FtcGFpZ25zLnJlYWRvbmx5IiwiY29udmVyc2F0aW9ucy5yZWFkb25seSIsImNvbnZlcnNhdGlvbnMud3JpdGUiLCJjb252ZXJzYXRpb25zL21lc3NhZ2UucmVhZG9ubHkiLCJjb252ZXJzYXRpb25zL21lc3NhZ2Uud3JpdGUiLCJjb250YWN0cy5yZWFkb25seSIsImNvbnRhY3RzLndyaXRlIiwiZm9ybXMucmVhZG9ubHkiLCJmb3Jtcy53cml0ZSIsImludm9pY2VzLnJlYWRvbmx5IiwiaW52b2ljZXMud3JpdGUiLCJpbnZvaWNlcy9zY2hlZHVsZS5yZWFkb25seSIsImludm9pY2VzL3NjaGVkdWxlLndyaXRlIiwiaW52b2ljZXMvdGVtcGxhdGUucmVhZG9ubHkiLCJpbnZvaWNlcy90ZW1wbGF0ZS53cml0ZSIsImxpbmtzLnJlYWRvbmx5IiwibGlua3Mud3JpdGUiLCJsb2NhdGlvbnMud3JpdGUiLCJsb2NhdGlvbnMucmVhZG9ubHkiLCJsb2NhdGlvbnMvY3VzdG9tVmFsdWVzLnJlYWRvbmx5IiwibG9jYXRpb25zL2N1c3RvbVZhbHVlcy53cml0ZSIsImxvY2F0aW9ucy9jdXN0b21GaWVsZHMucmVhZG9ubHkiLCJsb2NhdGlvbnMvY3VzdG9tRmllbGRzLndyaXRlIiwibG9jYXRpb25zL3Rhc2tzLnJlYWRvbmx5IiwibG9jYXRpb25zL3Rhc2tzLndyaXRlIiwibG9jYXRpb25zL3RhZ3MucmVhZG9ubHkiLCJsb2NhdGlvbnMvdGFncy53cml0ZSIsImxvY2F0aW9ucy90ZW1wbGF0ZXMucmVhZG9ubHkiLCJtZWRpYXMucmVhZG9ubHkiLCJtZWRpYXMud3JpdGUiLCJmdW5uZWxzL3JlZGlyZWN0LnJlYWRvbmx5IiwiZnVubmVscy9yZWRpcmVjdC53cml0ZSIsIm9wcG9ydHVuaXRpZXMucmVhZG9ubHkiLCJvcHBvcnR1bml0aWVzLndyaXRlIiwicGF5bWVudHMvb3JkZXJzLnJlYWRvbmx5IiwicGF5bWVudHMvdHJhbnNhY3Rpb25zLnJlYWRvbmx5IiwicGF5bWVudHMvc3Vic2NyaXB0aW9ucy5yZWFkb25seSIsInByb2R1Y3RzLnJlYWRvbmx5IiwicHJvZHVjdHMud3JpdGUiLCJwcm9kdWN0cy9wcmljZXMucmVhZG9ubHkiLCJwcm9kdWN0cy9wcmljZXMud3JpdGUiLCJzYWFzL2NvbXBhbnkucmVhZCIsInNhYXMvY29tcGFueS53cml0ZSIsInNhYXMvbG9jYXRpb24ucmVhZCIsInNhYXMvbG9jYXRpb24ud3JpdGUiLCJzdXJ2ZXlzLnJlYWRvbmx5IiwidXNlcnMucmVhZG9ubHkiLCJ1c2Vycy53cml0ZSIsIndvcmtmbG93cy5yZWFkb25seSIsInNuYXBzaG90cy5yZWFkb25seSIsIm9hdXRoLndyaXRlIiwib2F1dGgucmVhZG9ubHkiLCJzbmFwc2hvdHMud3JpdGUiXSwiY2xpZW50IjoiNjVmODQ5ZjAyYjlmNjgwMDM5MmQzYTM2IiwiY2xpZW50S2V5IjoiNjVmODQ5ZjAyYjlmNjgwMDM5MmQzYTM2LWx0eDBxZ2RwIn0sImlhdCI6MTcxNjQ1NTk1MC41MTEsImV4cCI6MTcxNjU0MjM1MC41MTF9.CYBObV8GBOYAUJ3d5wxpSu0nJlaRZm6lWRs3ZABRpCHpvJU5IW77sSQ74JoedJGyZ6-XvyTt96fR4la-GHG_LnLuon0WxLOJGdIsFuntTJwDx2WoPN5iOLO9j2ZBqGZJ6CjsFJg1kUd2p6wccCcH9GKYrPql73C6M7ZaNcAGRRx1LoArOEC27zQK138LP9Vef6E8gD6wmw97C1Xlx5lz-EgBxb8m8iJ8XTaOKaQ31-cT_8KYwIGUAX2_AeeVBaw49g8joiBzhJMbA0a5JcEavUN67V_HSvZHyB2zkAlQkiH3xHOsCaLQ-pB9huIfWkSLHaunV5ShQAavAGtWd1AqowNGAnNa-AJ4RupxpQ1EqBBytQT-5BL3UNH3WI84rGmVx6F5SQ44y_IEq8YXk9dO2LqXAHLpn-Um5U6K-_BNYm7gZvQFAuKq8dgpvf9I5SWxDd-bidZGdh59iWrag4jLvxiU7B-DF7orpGKlk8ug_arFQjstN3VJUOmBT-cv4jPppsJtSPWCVNNrLQRAUVqEuflr7GvPDpi8UHj5tv7okipm8SM6jOxwGuEAfBA_gX2heX8LOe3XazouTcxAsCrxeaeADvi08PXLSIaip-u5HNxGKOUy84zj8b7Fg5eS6vdB44Cppnezah8OCizmVDm1Lbi_WgO93QpCaUQMmYcuYnM';

    if ($auth_token !== $valid_token) {
        error_log('Received invalid token: ' . $auth_token);
    }

    return true;
}

function get_current_user_data() {
    // Check if user is logged in
    if (!is_user_logged_in()) {
        return new WP_Error('not_logged_in', 'User is not logged in', ['status' => 401]);
    }

    // Get the current logged-in user
    $current_user = wp_get_current_user();

    if (!empty($current_user->user_email)) {
        return [
            'status' => 'success',
            'email' => $current_user->user_email,
        ];
    }

    return new WP_Error('no_user_data', 'Unable to fetch user data', ['status' => 403]);
}

add_filter('rest_pre_serve_request', function($served, $result, $request) {
    header("Cache-Control: no-cache, must-revalidate, max-age=0");
    header("Expires: Wed, 11 Jan 1984 05:00:00 GMT");
    header("Pragma: no-cache");
    return $served;
}, 10, 3);

//jdn bundle default

// Add filter to handle default variation selection in OpenPOS bundles
function init_openpos_bundle_handlers() {
    if (function_exists('wc_get_product') && class_exists('WC_Product_Bundle')) {

    }
}
add_action('plugins_loaded', 'init_openpos_bundle_handlers', 20);
add_filter('op_product_data', 'handle_openpos_bundle_default_variations', 20, 3);
function handle_openpos_bundle_default_variations($response_data, $_product, $warehouse_id) {
    if (!class_exists('WC_Product_Bundle') || !function_exists('wc_get_product')) {
        return $response_data;
    }

    try {
        $product = wc_get_product($_product->ID);
        
        if ($product && $product->is_type('bundle') && !empty($response_data['bundles'])) {
            foreach ($response_data['bundles'] as &$bundle) {
                if (empty($bundle['product_id'])) continue;
                
                $bundled_product = wc_get_product($bundle['product_id']);
                if ($bundled_product && $bundled_product->is_type('variable')) {
                    // Get default attributes
                    $default_attributes = $bundled_product->get_default_attributes();
                    
                    // Set default values
                    if (!empty($bundle['variation'])) {
                        foreach ($bundle['variation'] as &$var) {
                            $attr_key = 'pa_' . $var['attribute'];
                            if (isset($default_attributes[$attr_key])) {
                                $var['selected'] = $default_attributes[$attr_key];
                                // Add debug output
                                error_log('Setting default attribute: ' . $attr_key . ' = ' . $default_attributes[$attr_key]);
                            }
                        }
                    }
                }
            }
        }
    } catch (Exception $e) {
        error_log('OpenPOS Bundle Variation Error: ' . $e->getMessage());
    }
    
    // Debug output
    error_log('Bundle data: ' . print_r($response_data['bundles'], true));
    
    return $response_data;
}

// Remove the modify_bundle_data function and filter since we're handling everything in handle_openpos_bundle_default_variations
remove_filter('op_product_bundle_data', 'modify_bundle_data', 10);

// Add proper textdomain loading for OpenPOS
function load_openpos_textdomain() {
    load_plugin_textdomain('openpos', false, dirname(plugin_basename(__FILE__)) . '/languages');
}
add_action('plugins_loaded', 'load_openpos_textdomain', 0);

// Add our bundle script to OpenPOS
function add_openpos_bundle_script() {
    // Only run on OpenPOS pages
    if (strpos($_SERVER['REQUEST_URI'], '/woocommerce-openpos/pos/') === false) {
        return;
    }

    // Add script to footer
    add_action('wp_footer', function() {
        ?>
        <script>
            // Check if script is loaded
            if (typeof window.OPENPOS_BUNDLE_DEBUG === 'undefined') {
                console.error('Bundle script not loaded!');
                
                // Try to load it again
                var script = document.createElement('script');
                script.src = '<?php echo plugins_url("woocommerce-openpos/pos/assets/js/bundle-default.js"); ?>';
                script.onload = function() {
                    console.log('Bundle script loaded manually');
                };
                document.body.appendChild(script);
            }
        </script>
        <?php
    }, 999);
}
add_action('init', 'add_openpos_bundle_script');

// Remove previous attempts
remove_action('init', 'add_openpos_bundle_handler');

// Add AJAX handler for bundle defaults
add_action('wp_ajax_get_bundle_defaults', 'get_bundle_defaults');
add_action('wp_ajax_nopriv_get_bundle_defaults', 'get_bundle_defaults');

function get_bundle_defaults() {
    try {
        error_log('Starting get_bundle_defaults()');
        
        // Get all bundle products
        $bundle_products = wc_get_products(array(
            'type' => 'bundle',
            'status' => 'publish',
            'limit' => -1
        ));

        error_log('Found bundle products: ' . count($bundle_products));
        $formatted_data = array();
        $processed_products = array(); // Track processed products to avoid duplicates

        foreach ($bundle_products as $bundle) {
            error_log('Processing bundle: ' . $bundle->get_name());
            
            // Get bundled items
            $bundled_items = $bundle->get_bundled_items();
            if (!$bundled_items) {
                error_log('No bundled items found for: ' . $bundle->get_name());
                continue;
            }

            foreach ($bundled_items as $bundled_item) {
                $product = $bundled_item->get_product();
                if (!$product) {
                    error_log('Could not get product for bundled item in: ' . $bundle->get_name());
                    continue;
                }

                $product_name = $product->get_name();
                
                // Skip if we've already processed this product
                if (isset($processed_products[$product_name])) {
                    continue;
                }

                error_log('Processing bundled product: ' . $product_name);
                error_log('Product type: ' . $product->get_type());

                // Check if product is variable or has variations
                if ($product->is_type('variable') || $product->get_type() === 'variable') {
                    $variations = $product->get_available_variations();
                    error_log('Found variations for ' . $product_name . ': ' . count($variations));

                    if (!empty($variations)) {
                        // First try to get default attributes
                        $default_attrs = $product->get_default_attributes();
                        error_log('Default attributes for ' . $product_name . ': ' . print_r($default_attrs, true));

                        // Check if this product should default to "10 series"
                        $should_default_to_10_series = in_array($product_name, [
                            'IV Therapy: TRIFECTA',
                            'LI-ECSW: ModWave Therapy',
                            'Prostate Injection: ABX | Nutrients | Ozone'
                        ]);

                        if ($should_default_to_10_series) {
                            $formatted_data[] = array(
                                'product_name' => $product_name,
                                'default_variation' => '10 series'
                            );
                            error_log('Added default "10 series" for ' . $product_name);
                        } elseif (!empty($default_attrs)) {
                            foreach ($default_attrs as $taxonomy => $term_slug) {
                                $term = get_term_by('slug', $term_slug, str_replace('attribute_', '', $taxonomy));
                                if ($term) {
                                    $formatted_data[] = array(
                                        'product_name' => $product_name,
                                        'default_variation' => $term->name
                                    );
                                    error_log('Added default variation from attributes: ' . $term->name . ' for ' . $product_name);
                                }
                            }
                        } else {
                            // Use first variation as default
                            $first_variation = $variations[0];
                            $formatted_data[] = array(
                                'product_name' => $product_name,
                                'default_variation' => '10 series'
                            );
                            error_log('Added first variation as default for ' . $product_name);
                        }

                        $processed_products[$product_name] = true;
                    }
                }
            }
        }

        // Ensure we have defaults for specific products
        $required_products = [
            'IV Therapy: TRIFECTA' => '10 series',
            'LI-ECSW: ModWave Therapy' => '10 series',
            'Prostate Injection: ABX | Nutrients | Ozone' => '10 series'
        ];

        foreach ($required_products as $name => $variation) {
            $found = false;
            foreach ($formatted_data as $data) {
                if ($data['product_name'] === $name) {
                    $found = true;
                    break;
                }
            }
            if (!$found) {
                $formatted_data[] = array(
                    'product_name' => $name,
                    'default_variation' => $variation
                );
                error_log('Added required default for ' . $name);
            }
        }

        error_log('Final formatted data: ' . print_r($formatted_data, true));
        wp_send_json($formatted_data);

    } catch (Exception $e) {
        error_log('Bundle defaults error: ' . $e->getMessage());
        error_log('Error trace: ' . $e->getTraceAsString());
        wp_send_json($required_products);
    }
}

// myaccount page
function embed_laravel_iframe() {
    ob_start();
    ?>
    <div id="user-emailDash" style="display: none;">
        <?php echo do_shortcode('[user_meta field="user_email"]'); ?>
    </div>

    <iframe id="laravel-iframe" src="https://forms.doctorsstudio.com/patient_dashboard/patient_old" width="100%" height="600"></iframe>

    <script>
        document.addEventListener('DOMContentLoaded', function () {
            const emailDiv = document.getElementById('user-emailDash');
            const email = emailDiv ? emailDiv.textContent.trim() : null;
            const iframe = document.getElementById('laravel-iframe');

            if (email) {
                if (iframe && iframe.contentWindow) {
                    const iframeOrigin = 'https://forms.doctorsstudio.com';

                    console.log('Preparing to send email to iframe:', email);

                    iframe.onload = function () {
                        console.log('Iframe loaded, sending email...');
                        iframe.contentWindow.postMessage({ email: email }, iframeOrigin);
                        console.log('Message sent successfully:', email);
                    };

                    // Fallback if iframe.onload doesn't trigger
                    setTimeout(() => {
                        if (iframe.contentWindow) {
                            iframe.contentWindow.postMessage({ email: email }, iframeOrigin);
                            console.log('Fallback message sent successfully:', email);
                        }
                    }, 3000);
                } else {
                    console.error('Iframe not found or inaccessible');
                }
            } else {
                console.error('Email not found in the specified div');
            }
        });
    </script>
    <?php
    return ob_get_clean();
}
add_shortcode('laravel_iframe', 'embed_laravel_iframe');


// Add custom REST API endpoint to fetch product meta data
function get_wc_order_metadata() {
    // Get the order ID from the URL query parameter
    if (!isset($_GET['order_id'])) {
        wp_die('Order ID is required.');
    }

    $order_id = intval($_GET['order_id']);

    if (!$order_id) {
        wp_die('Invalid Order ID.');
    }

    $order = wc_get_order($order_id);

    if (!$order) {
        wp_die('Order not found.');
    }

    $meta_data = [];

    foreach ($order->get_meta_data() as $meta) {
        $meta_data[$meta->key] = $meta->value;
    }

    // Return metadata as JSON
    wp_send_json($meta_data);
}

// Add a custom URL endpoint
add_action('init', function() {
    add_rewrite_rule('get-order-meta/?$', 'index.php?get_order_meta=1', 'top');
});

add_filter('query_vars', function($vars) {
    $vars[] = 'get_order_meta';
    return $vars;
});

add_action('template_redirect', function() {
    if (get_query_var('get_order_meta')) {
        get_wc_order_metadata();
        exit;
    }
});

/**
 * Disable WooCommerce's native "Customer Processing Order" email.
 *
 * The Django POS backend now handles sending the branded receipt email
 * for WooCommerce website orders via webhooks_order.py →
 * _send_woo_website_order_receipt(). Leaving the WooCommerce email
 * enabled would cause duplicate receipt emails to the customer.
 */
add_filter('woocommerce_email_enabled_customer_processing_order', '__return_false');

// Disable default user notification on registration
remove_action( 'register_new_user', 'wp_send_new_user_notifications' );
add_action( 'register_new_user', function( $user_id ) {
    // Send email only to admin, not to user
    wp_send_new_user_notifications( $user_id, 'admin' );
} );

////////////////////////////////////////////////

// Register custom REST API endpoint
add_action('rest_api_init', function () {
    register_rest_route('custom/v1', '/update-points', array(
        'methods' => 'POST',
        'callback' => 'pos_update_customer_points',
        'permission_callback' => 'pos_check_points_update_permission',
    ));
});
 
// Permission callback - check if request has proper authentication
function pos_check_points_update_permission($request) {
    // For WooCommerce API authentication, check if consumer key/secret are valid
    $auth_header = $request->get_header('authorization');
 
    if ($auth_header) {
        // Basic auth with consumer key/secret should be handled by WooCommerce
        return true;
    }
 
    // Fallback: check if user has WooCommerce manage permissions
    if (current_user_can('manage_woocommerce')) {
        return true;
    }
 
    return new WP_Error('rest_forbidden', 'You do not have permission to update points.', array('status' => 403));
}
 
// Main function to update customer points
function pos_update_customer_points($request) {
    $user_id = $request->get_param('user_id');
    $points = $request->get_param('points');
 
    if (!$user_id || !is_numeric($points)) {
        return new WP_Error('invalid_params', 'Invalid user_id or points value.', array('status' => 400));
    }
 
    // Log the attempt
    error_log("POS Points Update: Attempting to update points for user {$user_id} to {$points}");
 
    // Get current points data
    $current_balance = get_user_meta($user_id, '_ywpar_user_total_points', true);
    $current_earned = get_user_meta($user_id, '_ywpar_user_total_earned_points', true);
 
    // Convert to numbers, default to 0 if empty
    $current_balance = floatval($current_balance);
    $current_earned = floatval($current_earned);
 
    // Calculate earned points - only update if it's currently 0 or less than the new balance
    if ($current_earned == 0 || $current_earned < $points) {
        // Set earned points to be at least equal to the new balance
        // This ensures points_collected >= points_to_redeem
        $current_earned = max($current_earned, $points);
        error_log("POS Points Update: Updated total earned points to: {$current_earned}");
    } else {
        // Keep existing earned points if they're already higher
        error_log("POS Points Update: Keeping existing earned points: {$current_earned}");
    }
 
    // Update current balance (available points)
    $balance_keys = [
        '_ywpar_user_total_points',
        'wc_points_balance',
        '_yith_ywpar_customer_total_points', 
        'ywpar_user_total_points'
    ];
 
    // Update earned points (lifetime total)
    $earned_keys = [
        '_ywpar_user_total_earned_points',
        '_ywpar_points_earned',
        'ywpar_points_earned',
        '_ywpar_total_earned',
        '_ywpar_points_collected'
    ];
 
    $updated_keys = [];
 
    // Update current balance
    foreach ($balance_keys as $meta_key) {
        $result = update_user_meta($user_id, $meta_key, $points);
        if ($result !== false) {
            $updated_keys[] = $meta_key;
            error_log("POS Points Update: Successfully updated balance {$meta_key} = {$points} for user {$user_id}");
        }
    }
 
    // Update earned points (set to higher value to show lifetime earned)
    foreach ($earned_keys as $meta_key) {
        $result = update_user_meta($user_id, $meta_key, $current_earned);
        if ($result !== false) {
            $updated_keys[] = $meta_key;
            error_log("POS Points Update: Successfully updated earned {$meta_key} = {$current_earned} for user {$user_id}");
        }
    }
 
    // Try to use YITH plugin functions if available
    if (class_exists('YITH_WC_Points_Rewards_Customer')) {
        try {
            $customer = new YITH_WC_Points_Rewards_Customer($user_id);
            if (method_exists($customer, 'set_total_points')) {
                $customer->set_total_points($points);
                error_log("POS Points Update: Updated via YITH customer class");
            }
        } catch (Exception $e) {
            error_log("POS Points Update: YITH customer class update failed: " . $e->getMessage());
        }
    }
 
    // Try YITH main class methods
    if (function_exists('YITH_WC_Points_Rewards')) {
        try {
            $yith_instance = YITH_WC_Points_Rewards();
            if (method_exists($yith_instance, 'update_user_points')) {
                $yith_instance->update_user_points($user_id, $points);
                error_log("POS Points Update: Updated via YITH instance method");
            }
        } catch (Exception $e) {
            error_log("POS Points Update: YITH instance update failed: " . $e->getMessage());
        }
    }
 
    // Trigger any YITH hooks that might exist
    do_action('ywpar_update_user_points', $user_id, $points);
 
    // Return success response
    return array(
        'success' => true,
        'user_id' => $user_id,
        'points' => $points,
        'current_earned' => $current_earned,
        'updated_keys' => $updated_keys,
        'message' => 'Points updated successfully'
    );
}

////
// Enable multiple add-to-cart via URL arrays and keyed formats (with variation support)
add_action('template_redirect', function () {
  // Only run on front-end GET requests
  if (is_admin()) return;
  if ($_SERVER['REQUEST_METHOD'] !== 'GET') return;
  if (!function_exists('WC') || !WC()->cart) return;

  $added_any = false;

  // Helper: safe internal redirect (defaults to cart)
  $safe_redirect = function () {
    $redirect = wc_get_cart_url();

    if (!empty($_GET['redirect_to'])) {
      $maybe = esc_url_raw($_GET['redirect_to']);
      // Only allow same-host redirects to avoid open redirects
      $host_maybe = parse_url($maybe, PHP_URL_HOST);
      $host_here  = isset($_SERVER['HTTP_HOST']) ? $_SERVER['HTTP_HOST'] : '';
      if (!$host_maybe || strcasecmp($host_maybe, $host_here) === 0) {
        $redirect = $maybe;
      }
    }

    wp_safe_redirect($redirect);
    exit;
  };

  // 1) Array format:
  // /cart/?add-to-cart[]=ID&quantity[]=QTY&variation_id[]=VARIATION_ID (0 or omit for simple)
  if (isset($_GET['add-to-cart']) && is_array($_GET['add-to-cart'])) {
    $product_ids   = array_values(array_map('intval', (array) $_GET['add-to-cart']));
    $quantities    = array_values(isset($_GET['quantity']) ? array_map('intval', (array) $_GET['quantity']) : []);
    $variation_ids = array_values(isset($_GET['variation_id']) ? array_map('intval', (array) $_GET['variation_id']) : []);

    // Safety cap to prevent abuse
    $max_items = 50;
    $count = min(count($product_ids), $max_items);

    for ($i = 0; $i < $count; $i++) {
      $pid = (int) ($product_ids[$i] ?? 0);
      if ($pid <= 0) continue;

      $qty = (int) ($quantities[$i] ?? 1);
      if ($qty < 1) $qty = 1;

      $vid = (int) ($variation_ids[$i] ?? 0);

      if ($vid > 0) {
        // Variable product (variation)
        WC()->cart->add_to_cart($pid, $qty, $vid);
      } else {
        // Simple product
        WC()->cart->add_to_cart($pid, $qty);
      }
      $added_any = true;
    }

    if ($added_any) {
      $safe_redirect();
    }
  }

  // 2) Keyed format:
  // /cart/?add-to-cart[PRODUCT_ID]=QTY&variation_id[PRODUCT_ID]=VARIATION_ID
  foreach ($_GET as $key => $value) {
    if (preg_match('/^add-to-cart\[(\d+)\]$/', (string) $key, $m)) {
      $pid = (int) $m[1];
      $qty = max(1, (int) $value);

      $vid = 0;
      if (isset($_GET['variation_id']) && is_array($_GET['variation_id'])) {
        $vid = (int) ($_GET['variation_id'][$pid] ?? 0);
      }

      if ($pid > 0) {
        if ($vid > 0) {
          WC()->cart->add_to_cart($pid, $qty, $vid);
        } else {
          WC()->cart->add_to_cart($pid, $qty);
        }
        $added_any = true;
      }
    }
  }

  if ($added_any) {
    $safe_redirect();
  }
}, 11);
////




/**
 * WooCommerce Bundle Pricing Hook for DS POS Orders
 * 
 * This code should be added to your theme's functions.php file or as a custom plugin.
 * It prevents WooCommerce from recalculating prices for bundle items from DS POS.
 */

// Hook into order item creation to preserve custom pricing
add_action('woocommerce_checkout_create_order_line_item', 'preserve_ds_pos_bundle_pricing', 10, 4);

function preserve_ds_pos_bundle_pricing($item, $cart_item_key, $values, $order) {
    // Only process if this is a DS POS order
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Check if this is a bundle parent item
    if ($item->get_meta('_ds_bundle_parent_item') === 'true') {
        $bundle_price = $item->get_meta('_ds_line_total');
        if ($bundle_price) {
            $item->set_subtotal($bundle_price);
            $item->set_total($bundle_price);
            error_log("DS POS: Set bundle parent price to $" . $bundle_price);
        }
    }
    
    // Check if this is a bundle child item
    if ($item->get_meta('_ds_bundle_child_item') === 'true') {
        $item->set_subtotal(0);
        $item->set_total(0);
        error_log("DS POS: Set bundle child price to $0");
    }
}

// Hook into order creation via REST API
add_action('woocommerce_rest_insert_shop_order_object', 'preserve_ds_pos_bundle_pricing_rest', 10, 3);

function preserve_ds_pos_bundle_pricing_rest($order, $request, $creating) {
    if (!$creating || !is_ds_pos_order($order)) {
        return;
    }
    
    error_log("DS POS: Processing bundle pricing for order " . $order->get_id());
    
    $bundle_parent_found = false;
    $bundle_children_count = 0;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Log all meta data for debugging
        $all_meta = [];
        foreach ($item->get_meta_data() as $meta) {
            $all_meta[$meta->key] = $meta->value;
        }
        error_log("DS POS: Item {$item_id} ({$item->get_name()}) meta: " . json_encode($all_meta));
        
        // Handle bundle parent items - check multiple possible meta keys
        if ($item->get_meta('_ds_bundle_parent_item') === 'true' || 
            $item->get_meta('Bundle Type') === 'Parent Item') {
            
            $bundle_price = $item->get_meta('_ds_line_total') ?: $item->get_meta('Original Bundle Price');
            if ($bundle_price) {
                $item->set_subtotal($bundle_price);
                $item->set_total($bundle_price);
                $item->save();
                $bundle_parent_found = true;
                error_log("DS POS: Updated bundle parent item {$item_id} ({$item->get_name()}) to $" . $bundle_price);
            }
        }
        
        // Handle bundle child items
        if ($item->get_meta('_ds_bundle_child_item') === 'true' || 
            $item->get_meta('_ds_bundle_parent_id')) {
            
            $item->set_subtotal(0);
            $item->set_total(0);
            $item->save();
            $bundle_children_count++;
            error_log("DS POS: Updated bundle child item {$item_id} ({$item->get_name()}) to $0");
        }
    }
    
    error_log("DS POS: Bundle processing complete - Parent found: " . ($bundle_parent_found ? 'YES' : 'NO') . ", Children: {$bundle_children_count}");
    
    // If we found a bundle, force the order total to match our bundle pricing
    if ($bundle_parent_found) {
        // Calculate what the total should be (bundle price + any non-bundle items)
        $expected_total = 0;
        foreach ($order->get_items() as $item) {
            $expected_total += floatval($item->get_total());
        }
        
        error_log("DS POS: Expected order total: $" . $expected_total);
        
        // Don't force total here - let WooCommerce calculate it properly after our pricing is set
        // $order->set_total($expected_total);
        // error_log("DS POS: Forced order total to $" . $expected_total);
    }
    
    // Don't recalculate - this causes WooCommerce to override our pricing
    // $order->calculate_totals();
    $order->save();
}

// AGGRESSIVE APPROACH: Hook into the calculation process itself
add_action('woocommerce_order_before_calculate_totals', 'lock_ds_pos_bundle_pricing', 5, 2);
add_action('woocommerce_order_after_calculate_totals', 'restore_ds_pos_bundle_pricing', 15, 2);

function lock_ds_pos_bundle_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Store the correct pricing before WooCommerce messes with it
    foreach ($order->get_items() as $item_id => $item) {
        if ($item->get_meta('_ds_bundle_parent_item') === 'true' || 
            $item->get_meta('Bundle Type') === 'Parent Item') {
            $bundle_price = $item->get_meta('Original Bundle Price');
            if ($bundle_price) {
                $order->add_meta_data("_ds_pos_parent_{$item_id}_subtotal", $bundle_price, true);
                $order->add_meta_data("_ds_pos_parent_{$item_id}_total", $bundle_price, true);
                error_log("DS POS: Locking parent item {$item_id} at $" . $bundle_price);
            }
        }
        
        if ($item->get_meta('_ds_bundle_child_item') === 'true' || 
            $item->get_meta('_ds_bundle_parent_id')) {
            $order->add_meta_data("_ds_pos_child_{$item_id}_subtotal", '0', true);
            $order->add_meta_data("_ds_pos_child_{$item_id}_total", '0', true);
            error_log("DS POS: Locking child item {$item_id} at $0");
        }
    }
}

function restore_ds_pos_bundle_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    $needs_save = false;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Restore parent pricing
        $locked_subtotal = $order->get_meta("_ds_pos_parent_{$item_id}_subtotal");
        $locked_total = $order->get_meta("_ds_pos_parent_{$item_id}_total");
        
        if ($locked_subtotal && $locked_total) {
            $item->set_subtotal($locked_subtotal);
            $item->set_total($locked_total);
            $needs_save = true;
            error_log("DS POS: FORCE restored parent item {$item_id} to $" . $locked_total);
            
            // Clean up
            $order->delete_meta_data("_ds_pos_parent_{$item_id}_subtotal");
            $order->delete_meta_data("_ds_pos_parent_{$item_id}_total");
        }
        
        // Restore child pricing
        $locked_subtotal = $order->get_meta("_ds_pos_child_{$item_id}_subtotal");
        $locked_total = $order->get_meta("_ds_pos_child_{$item_id}_total");
        
        if ($locked_subtotal !== '' && $locked_total !== '') {
            $item->set_subtotal(0);
            $item->set_total(0);
            $needs_save = true;
            error_log("DS POS: FORCE restored child item {$item_id} to $0");
            
            // Clean up
            $order->delete_meta_data("_ds_pos_child_{$item_id}_subtotal");
            $order->delete_meta_data("_ds_pos_child_{$item_id}_total");
        }
    }
    
    if ($needs_save) {
        // Force save the items
        foreach ($order->get_items() as $item) {
            $item->save();
        }
        error_log("DS POS: FORCE saved all bundle items with correct pricing");
    }
}

// Helper function to check if this is a DS POS order
function is_ds_pos_order($order) {
    if (!$order) {
        return false;
    }
    
    // Don't run for refunds or other objects - only for actual orders
    // WC_Order_Refund doesn't have get_created_via() method
    if (!is_a($order, 'WC_Order') || is_a($order, 'WC_Order_Refund')) {
        return false;
    }
    
    // Check multiple indicators that this is a DS POS order
    $created_via = $order->get_created_via();
    $source = $order->get_meta('source');
    $order_source = $order->get_meta('_order_source');
    $pos_order_id = $order->get_meta('_pos_order_id');
    
    return (
        $created_via === 'DS POS' ||
        $source === 'DS POS' ||
        $order_source === 'DS POS' ||
        !empty($pos_order_id)
    );
}

// Hook into product price calculation for bundle items
add_filter('woocommerce_product_get_price', 'override_bundle_item_price', 10, 2);
add_filter('woocommerce_product_variation_get_price', 'override_bundle_item_price', 10, 2);

function override_bundle_item_price($price, $product) {
    // Only override during order creation/calculation
    if (!doing_action('woocommerce_rest_insert_shop_order_object') && 
        !doing_action('woocommerce_checkout_create_order_line_item')) {
        return $price;
    }
    
    // Check if we're in a DS POS context
    global $ds_pos_bundle_context;
    if (!$ds_pos_bundle_context) {
        return $price;
    }
    
    // Return custom price if set
    if (isset($ds_pos_bundle_context['product_' . $product->get_id()])) {
        return $ds_pos_bundle_context['product_' . $product->get_id()];
    }
    
    return $price;
}

// Add admin notice for successful installation
add_action('admin_notices', 'ds_pos_bundle_pricing_notice');

function ds_pos_bundle_pricing_notice() {
    if (current_user_can('manage_options')) {
        echo '<div class="notice notice-success"><p><strong>DS POS Bundle Pricing:</strong> Custom bundle pricing hooks are active and will preserve DS POS order pricing.</p></div>';
    }
}

// NUCLEAR OPTION: Hook into order display/rendering
add_filter('woocommerce_order_get_items', 'force_ds_pos_bundle_display', 10, 2);

function force_ds_pos_bundle_display($items, $order) {
    if (!is_ds_pos_order($order)) {
        return $items;
    }
    
    // Force correct pricing on every display
    foreach ($items as $item_id => $item) {
        if ($item->get_meta('_ds_bundle_parent_item') === 'true' || 
            $item->get_meta('Bundle Type') === 'Parent Item') {
            $bundle_price = $item->get_meta('Original Bundle Price');
            if ($bundle_price) {
                $item->set_subtotal($bundle_price);
                $item->set_total($bundle_price);
                error_log("DS POS: NUCLEAR - Force display parent {$item_id} at $" . $bundle_price);
            }
        }
        
        if ($item->get_meta('_ds_bundle_child_item') === 'true' || 
            $item->get_meta('_ds_bundle_parent_id')) {
            $item->set_subtotal(0);
            $item->set_total(0);
            error_log("DS POS: NUCLEAR - Force display child {$item_id} at $0");
        }
    }
    
    return $items;
}

// Hook into line item pricing display
add_filter('woocommerce_order_item_get_subtotal', 'force_ds_pos_line_item_subtotal', 10, 2);
add_filter('woocommerce_order_item_get_total', 'force_ds_pos_line_item_total', 10, 2);

function force_ds_pos_line_item_subtotal($subtotal, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $subtotal;
    }
    
    if ($item->get_meta('_ds_bundle_parent_item') === 'true' || 
        $item->get_meta('Bundle Type') === 'Parent Item') {
        $bundle_price = $item->get_meta('Original Bundle Price');
        if ($bundle_price) {
            error_log("DS POS: NUCLEAR - Force line item subtotal to $" . $bundle_price);
            return $bundle_price;
        }
    }
    
    if ($item->get_meta('_ds_bundle_child_item') === 'true' || 
        $item->get_meta('_ds_bundle_parent_id')) {
        error_log("DS POS: NUCLEAR - Force line item subtotal to $0");
        return 0;
    }
    
    return $subtotal;
}

function force_ds_pos_line_item_total($total, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $total;
    }
    
    if ($item->get_meta('_ds_bundle_parent_item') === 'true' || 
        $item->get_meta('Bundle Type') === 'Parent Item') {
        $bundle_price = $item->get_meta('Original Bundle Price');
        if ($bundle_price) {
            error_log("DS POS: NUCLEAR - Force line item total to $" . $bundle_price);
            return $bundle_price;
        }
    }
    
    if ($item->get_meta('_ds_bundle_child_item') === 'true' || 
        $item->get_meta('_ds_bundle_parent_id')) {
        error_log("DS POS: NUCLEAR - Force line item total to $0");
        return 0;
    }
    
    return $total;
}

// Hook into order total calculation
add_filter('woocommerce_order_get_total', 'force_ds_pos_bundle_total', 10, 2);

function force_ds_pos_bundle_total($total, $order) {
    if (!is_ds_pos_order($order)) {
        return $total;
    }
    
    // Calculate correct total from ALL line items (bundle parents + standalone items)
    // Bundle children contribute $0 so they don't affect the sum
    $correct_total = 0;
    $has_bundle_items = false;
    foreach ($order->get_items() as $item) {
        $is_bundle_child = ($item->get_meta('_ds_bundle_child_item') === 'true' || 
                           $item->get_meta('_ds_bundle_parent_id'));
        $is_bundle_parent = ($item->get_meta('_ds_bundle_parent_item') === 'true' || 
                            $item->get_meta('Bundle Type') === 'Parent Item');
        
        if ($is_bundle_parent) {
            $has_bundle_items = true;
            $bundle_price = $item->get_meta('Original Bundle Price');
            if ($bundle_price) {
                $correct_total += floatval($bundle_price);
            } else {
                $correct_total += floatval($item->get_total());
            }
        } elseif ($is_bundle_child) {
            // Bundle children contribute $0 — skip
            $has_bundle_items = true;
        } else {
            // Standalone (non-bundle) items — add their total
            $correct_total += floatval($item->get_total());
        }
    }
    
    // Include fee lines (e.g., discounts)
    foreach ($order->get_fees() as $fee) {
        $correct_total += floatval($fee->get_total());
    }
    
    if ($has_bundle_items && $correct_total > 0) {
        error_log("DS POS: NUCLEAR - Force order total from $" . $total . " to $" . $correct_total);
        return $correct_total;
    }
    
    return $total;
}

error_log("DS POS Bundle Pricing hooks loaded successfully");
////////////////////



// if server goes critical error remove this
// DS POS: Credited Services - NUCLEAR APPROACH (Same as Bundle Hook)

if (!function_exists('is_ds_pos_order')) {
    function is_ds_pos_order($order) {
        if (!$order) return false;
        return $order->get_meta('ds_pos_order') === '1';
    }
}

// 1. LOCK PRICING BEFORE CALCULATION
add_action('woocommerce_order_before_calculate_totals', 'lock_ds_pos_credited_pricing', 5, 2);

function lock_ds_pos_credited_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) return;
    
    foreach ($order->get_items() as $item_id => $item) {
        if ($item->get_meta('ds_is_credited_service') === '1') {
            $order->add_meta_data("_ds_pos_credited_{$item_id}_subtotal", '0', true);
            $order->add_meta_data("_ds_pos_credited_{$item_id}_total", '0', true);
            error_log("DS POS: Locking credited item {$item_id} at $0");
        }
    }
}

// 2. RESTORE PRICING AFTER CALCULATION
add_action('woocommerce_order_after_calculate_totals', 'restore_ds_pos_credited_pricing', 15, 2);

function restore_ds_pos_credited_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) return;
    
    $needs_save = false;
    foreach ($order->get_items() as $item_id => $item) {
        $locked_subtotal = $order->get_meta("_ds_pos_credited_{$item_id}_subtotal");
        $locked_total = $order->get_meta("_ds_pos_credited_{$item_id}_total");
        
        if ($locked_subtotal !== '' && $locked_total !== '') {
            $item->set_subtotal(0);
            $item->set_total(0);
            $needs_save = true;
            error_log("DS POS: FORCE restored credited item {$item_id} to $0");
            
            $order->delete_meta_data("_ds_pos_credited_{$item_id}_subtotal");
            $order->delete_meta_data("_ds_pos_credited_{$item_id}_total");
        }
    }
    
    if ($needs_save) {
        foreach ($order->get_items() as $item) {
            $item->save();
        }
        error_log("DS POS: FORCE saved all credited items with $0 pricing");
    }
}

// 3. FORCE LINE ITEM DISPLAY
add_filter('woocommerce_order_item_get_subtotal', 'force_ds_pos_credited_subtotal', 10, 2);
add_filter('woocommerce_order_item_get_total', 'force_ds_pos_credited_total', 10, 2);

function force_ds_pos_credited_subtotal($subtotal, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) return $subtotal;
    
    if ($item->get_meta('ds_is_credited_service') === '1') {
        error_log("DS POS: NUCLEAR - Force credited subtotal to $0");
        return 0;
    }
    return $subtotal;
}

function force_ds_pos_credited_total($total, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) return $total;
    
    if ($item->get_meta('ds_is_credited_service') === '1') {
        error_log("DS POS: NUCLEAR - Force credited total to $0");
        return 0;
    }
    return $total;
}

// 4. FORCE ORDER TOTAL CALCULATION (KEY MISSING PIECE!)
add_filter('woocommerce_order_get_total', 'force_ds_pos_credited_order_total', 10, 2);

function force_ds_pos_credited_order_total($total, $order) {
    if (!is_ds_pos_order($order)) return $total;
    
    // Check if order has credited services
    $has_credited = false;
    foreach ($order->get_items() as $item) {
        if ($item->get_meta('ds_is_credited_service') === '1') {
            $has_credited = true;
            break;
        }
    }
    
    if ($has_credited) {
        error_log("DS POS: NUCLEAR - Force order total to $0 (credited service)");
        return 0;
    }
    
    return $total;
}

// 5. ORDER CREATION HOOK
add_action('woocommerce_rest_insert_shop_order_object', function($order, $request, $creating) {
    if ($creating && is_ds_pos_order($order)) {
        foreach ($order->get_items() as $item) {
            if ($item->get_meta('ds_is_credited_service') === '1') {
                $item->set_subtotal(0);
                $item->set_total(0);
                $item->save();
            }
        }
        $order->update_meta_data('_ds_pos_credited_processed', '1');
        $order->save();
    }
}, 20, 3);
/////////



/**
 * WooCommerce Trial Membership Pricing Hook for DS POS Orders
 * 
 * This code should be added to your theme's functions.php file or as a custom plugin.
 * It prevents WooCommerce from recalculating prices for trial membership items from DS POS.
 */

// Hook into order item creation to preserve trial membership pricing
add_action('woocommerce_checkout_create_order_line_item', 'preserve_ds_pos_trial_membership_pricing', 10, 4);

function preserve_ds_pos_trial_membership_pricing($item, $cart_item_key, $values, $order) {
    // Only process if this is a DS POS order
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Check if this is a trial membership item
    if ($item->get_meta('_trial_membership') === 'true') {
        $item->set_subtotal(0);
        $item->set_total(0);
        error_log("DS POS: Set trial membership price to $0 for: " . $item->get_name());
    }
}

// Hook into order creation via REST API
add_action('woocommerce_rest_insert_shop_order_object', 'preserve_ds_pos_trial_membership_pricing_rest', 10, 3);

function preserve_ds_pos_trial_membership_pricing_rest($order, $request, $creating) {
    if (!$creating || !is_ds_pos_order($order)) {
        return;
    }
    
    error_log("DS POS: Processing trial membership pricing for order " . $order->get_id());
    
    $trial_memberships_found = 0;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Log all meta data for debugging
        $all_meta = [];
        foreach ($item->get_meta_data() as $meta) {
            $all_meta[$meta->key] = $meta->value;
        }
        error_log("DS POS: Item {$item_id} ({$item->get_name()}) meta: " . json_encode($all_meta));
        
        // Handle trial membership items - check for trial membership meta
        if ($item->get_meta('_trial_membership') === 'true') {
            $actual_price = $item->get_meta('_actual_membership_price');
            $trial_period = $item->get_meta('_trial_period_months');
            
            // Set to $0 for trial period
            $item->set_subtotal(0);
            $item->set_total(0);
            $item->save();
            $trial_memberships_found++;
            
            error_log("DS POS: Updated trial membership item {$item_id} ({$item->get_name()}) to $0 (actual price: $" . $actual_price . ", trial period: " . $trial_period . " months)");
        }
    }
    
    error_log("DS POS: Trial membership processing complete - Found: {$trial_memberships_found} trial memberships");
    
    $order->save();
}

// AGGRESSIVE APPROACH: Hook into the calculation process itself
add_action('woocommerce_order_before_calculate_totals', 'lock_ds_pos_trial_membership_pricing', 5, 2);
add_action('woocommerce_order_after_calculate_totals', 'restore_ds_pos_trial_membership_pricing', 15, 2);

function lock_ds_pos_trial_membership_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Store the correct pricing before WooCommerce messes with it
    foreach ($order->get_items() as $item_id => $item) {
        if ($item->get_meta('_trial_membership') === 'true') {
            $order->add_meta_data("_ds_pos_trial_{$item_id}_subtotal", '0', true);
            $order->add_meta_data("_ds_pos_trial_{$item_id}_total", '0', true);
            error_log("DS POS: Locking trial membership item {$item_id} at $0");
        }
    }
}

function restore_ds_pos_trial_membership_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    $needs_save = false;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Restore trial membership pricing
        $locked_subtotal = $order->get_meta("_ds_pos_trial_{$item_id}_subtotal");
        $locked_total = $order->get_meta("_ds_pos_trial_{$item_id}_total");
        
        if ($locked_subtotal !== '' && $locked_total !== '') {
            $item->set_subtotal(0);
            $item->set_total(0);
            $needs_save = true;
            error_log("DS POS: FORCE restored trial membership item {$item_id} to $0");
            
            // Clean up
            $order->delete_meta_data("_ds_pos_trial_{$item_id}_subtotal");
            $order->delete_meta_data("_ds_pos_trial_{$item_id}_total");
        }
    }
    
    if ($needs_save) {
        // Force save the items
        foreach ($order->get_items() as $item) {
            $item->save();
        }
        error_log("DS POS: FORCE saved all trial membership items with $0 pricing");
    }
}

// Helper function to check if this is a DS POS order (reuse from bundle hook)
if (!function_exists('is_ds_pos_order')) {
    function is_ds_pos_order($order) {
        if (!$order) {
            return false;
        }
        
        // Check multiple indicators that this is a DS POS order
        $created_via = $order->get_created_via();
        $source = $order->get_meta('source');
        $order_source = $order->get_meta('_order_source');
        $pos_order_id = $order->get_meta('_pos_order_id');
        
        return (
            $created_via === 'DS POS' ||
            $source === 'DS POS' ||
            $order_source === 'DS POS' ||
            !empty($pos_order_id)
        );
    }
}

// NUCLEAR OPTION: Hook into order display/rendering for trial memberships
add_filter('woocommerce_order_get_items', 'force_ds_pos_trial_membership_display', 10, 2);

function force_ds_pos_trial_membership_display($items, $order) {
    if (!is_ds_pos_order($order)) {
        return $items;
    }
    
    // Force correct pricing on every display
    foreach ($items as $item_id => $item) {
        if ($item->get_meta('_trial_membership') === 'true') {
            $item->set_subtotal(0);
            $item->set_total(0);
            error_log("DS POS: NUCLEAR - Force display trial membership {$item_id} at $0");
        }
    }
    
    return $items;
}

// Hook into line item pricing display for trial memberships
add_filter('woocommerce_order_item_get_subtotal', 'force_ds_pos_trial_membership_line_item_subtotal', 10, 2);
add_filter('woocommerce_order_item_get_total', 'force_ds_pos_trial_membership_line_item_total', 10, 2);

function force_ds_pos_trial_membership_line_item_subtotal($subtotal, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $subtotal;
    }
    
    if ($item->get_meta('_trial_membership') === 'true') {
        error_log("DS POS: NUCLEAR - Force trial membership line item subtotal to $0");
        return 0;
    }
    
    return $subtotal;
}

function force_ds_pos_trial_membership_line_item_total($total, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $total;
    }
    
    if ($item->get_meta('_trial_membership') === 'true') {
        error_log("DS POS: NUCLEAR - Force trial membership line item total to $0");
        return 0;
    }
    
    return $total;
}

// Hook into product price calculation for trial membership items
add_filter('woocommerce_product_get_price', 'override_trial_membership_item_price', 10, 2);
add_filter('woocommerce_product_variation_get_price', 'override_trial_membership_item_price', 10, 2);

function override_trial_membership_item_price($price, $product) {
    // Only override during order creation/calculation
    if (!doing_action('woocommerce_rest_insert_shop_order_object') && 
        !doing_action('woocommerce_checkout_create_order_line_item')) {
        return $price;
    }
    
    // Check if we're in a DS POS trial membership context
    global $ds_pos_trial_membership_context;
    if (!$ds_pos_trial_membership_context) {
        return $price;
    }
    
    // Return $0 price if this product is marked as trial membership
    if (isset($ds_pos_trial_membership_context['product_' . $product->get_id()])) {
        return 0;
    }
    
    return $price;
}

// Hook into order total calculation to ensure trial memberships don't affect totals
add_filter('woocommerce_order_get_total', 'adjust_ds_pos_trial_membership_total', 10, 2);

function adjust_ds_pos_trial_membership_total($total, $order) {
    if (!is_ds_pos_order($order)) {
        return $total;
    }
    
    // Check if we have trial memberships and adjust total if needed
    $trial_membership_adjustment = 0;
    foreach ($order->get_items() as $item) {
        if ($item->get_meta('_trial_membership') === 'true') {
            // Make sure trial memberships contribute $0 to total
            $current_item_total = $item->get_total();
            if ($current_item_total > 0) {
                $trial_membership_adjustment -= $current_item_total;
                error_log("DS POS: Adjusting order total by -$" . $current_item_total . " for trial membership: " . $item->get_name());
            }
        }
    }
    
    if ($trial_membership_adjustment != 0) {
        $adjusted_total = $total + $trial_membership_adjustment;
        error_log("DS POS: NUCLEAR - Adjusted order total from $" . $total . " to $" . $adjusted_total . " (adjustment: $" . $trial_membership_adjustment . ")");
        return max(0, $adjusted_total); // Ensure total never goes negative
    }
    
    return $total;
}

// Add admin notice for successful installation
add_action('admin_notices', 'ds_pos_trial_membership_pricing_notice');

function ds_pos_trial_membership_pricing_notice() {
    if (current_user_can('manage_options')) {
        echo '<div class="notice notice-success"><p><strong>DS POS Trial Membership Pricing:</strong> Custom trial membership pricing hooks are active and will preserve $0 pricing for trial memberships.</p></div>';
    }
}

// Advanced: Hook into WooCommerce Subscriptions to ensure trial memberships create proper subscriptions
add_filter('woocommerce_subscriptions_product_sign_up_fee', 'force_trial_membership_signup_fee', 10, 2);

function force_trial_membership_signup_fee($sign_up_fee, $product) {
    // Check if we're processing a DS POS trial membership
    global $ds_pos_trial_membership_context;
    if ($ds_pos_trial_membership_context && 
        isset($ds_pos_trial_membership_context['product_' . $product->get_id()])) {
        error_log("DS POS: Setting trial membership signup fee to $0 for product " . $product->get_id());
        return 0;
    }
    
    return $sign_up_fee;
}

error_log("DS POS Trial Membership Pricing hooks loaded successfully");


/**
 * WooCommerce Modified Membership Pricing Hook for DS POS Orders
 * 
 * This code should be added to your theme's functions.php file or as a custom plugin.
 * It prevents WooCommerce/ATUM from recalculating prices for membership items from DS POS
 * when the price has been manually modified (discounted) during checkout.
 */

// Hook into order item creation to preserve modified membership pricing
add_action('woocommerce_checkout_create_order_line_item', 'preserve_ds_pos_modified_membership_pricing', 10, 4);

function preserve_ds_pos_modified_membership_pricing($item, $cart_item_key, $values, $order) {
    // Only process if this is a DS POS order
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Check if this is an item with modified pricing (any product)
    if (has_modified_pricing($item)) {
        $modified_price = get_modified_price($item);
        if ($modified_price !== false) {
            $item->set_subtotal($modified_price);
            $item->set_total($modified_price);
            error_log("DS POS: Set modified membership price to $" . $modified_price . " for: " . $item->get_name());
        }
    }
}

// Hook into order creation via REST API
add_action('woocommerce_rest_insert_shop_order_object', 'preserve_ds_pos_modified_membership_pricing_rest', 10, 3);

function preserve_ds_pos_modified_membership_pricing_rest($order, $request, $creating) {
    if (!$creating || !is_ds_pos_order($order)) {
        return;
    }
    
    error_log("DS POS: Processing modified membership pricing for order " . $order->get_id());
    
    $modified_memberships_found = 0;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Log all meta data for debugging
        $all_meta = [];
        foreach ($item->get_meta_data() as $meta) {
            $all_meta[$meta->key] = $meta->value;
        }
        error_log("DS POS: Item {$item_id} ({$item->get_name()}) meta: " . json_encode($all_meta));
        
        // Handle modified pricing items - check for pricing modifications
        if (has_modified_pricing($item)) {
            $original_price = $item->get_meta('_ds_pos_original_price');
            $modified_price = $item->get_meta('_ds_pos_modified_price');
            $discount_amount = $item->get_meta('_ds_pos_discount_amount');
            
            // If we have modified price metadata, use it
            if ($modified_price !== '' && is_numeric($modified_price)) {
                $rounded_price = round(floatval($modified_price), 2);
                // 🔥 CRITICAL FIX: Multiply per-unit price by quantity for line total
                $quantity = $item->get_quantity();
                $line_total = $rounded_price * $quantity;
                $item->set_subtotal($line_total);
                $item->set_total($line_total);
                $item->save();
                $modified_memberships_found++;
                
                error_log("DS POS: Updated modified membership item {$item_id} ({$item->get_name()}) to $" . $line_total . " (per-unit: $" . $rounded_price . " × {$quantity}, original: $" . $original_price . ", discount: $" . $discount_amount . ")");
            }
            // Fallback: calculate from original price and discount
            else if ($original_price !== '' && $discount_amount !== '' && 
                     is_numeric($original_price) && is_numeric($discount_amount)) {
                $final_price_per_unit = floatval($original_price) - floatval($discount_amount);
                $final_price_per_unit = max(0, $final_price_per_unit); // Ensure non-negative
                $final_price_per_unit = round($final_price_per_unit, 2); // Round to 2 decimal places
                
                // 🔥 CRITICAL FIX: Multiply per-unit price by quantity for line total
                $quantity = $item->get_quantity();
                $line_total = $final_price_per_unit * $quantity;
                
                $item->set_subtotal($line_total);
                $item->set_total($line_total);
                $item->save();
                $modified_memberships_found++;
                
                error_log("DS POS: Calculated modified membership item {$item_id} ({$item->get_name()}) to $" . $line_total . " (per-unit: $" . $final_price_per_unit . " × {$quantity}, original: $" . $original_price . " - discount: $" . $discount_amount . ")");
            }
        }
    }
    
    error_log("DS POS: Modified membership processing complete - Found: {$modified_memberships_found} modified memberships");
    
    $order->save();
}

// AGGRESSIVE APPROACH: Hook into the calculation process itself
add_action('woocommerce_order_before_calculate_totals', 'lock_ds_pos_modified_membership_pricing', 5, 2);
add_action('woocommerce_order_after_calculate_totals', 'restore_ds_pos_modified_membership_pricing', 15, 2);

function lock_ds_pos_modified_membership_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Store the correct modified pricing before WooCommerce messes with it
    foreach ($order->get_items() as $item_id => $item) {
        if (has_modified_pricing($item)) {
            $modified_price = get_modified_price($item);
            if ($modified_price !== false) {
                $order->add_meta_data("_ds_pos_modified_{$item_id}_subtotal", $modified_price, true);
                $order->add_meta_data("_ds_pos_modified_{$item_id}_total", $modified_price, true);
                error_log("DS POS: Locking modified membership item {$item_id} at $" . $modified_price);
            }
        }
    }
}

function restore_ds_pos_modified_membership_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    $needs_save = false;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Restore modified membership pricing
        $locked_subtotal = $order->get_meta("_ds_pos_modified_{$item_id}_subtotal");
        $locked_total = $order->get_meta("_ds_pos_modified_{$item_id}_total");
        
        if ($locked_subtotal !== '' && $locked_total !== '') {
            $rounded_subtotal = round(floatval($locked_subtotal), 2);
            $rounded_total = round(floatval($locked_total), 2);
            $item->set_subtotal($rounded_subtotal);
            $item->set_total($rounded_total);
            $needs_save = true;
            error_log("DS POS: FORCE restored modified membership item {$item_id} to $" . $locked_subtotal);
            
            // Clean up
            $order->delete_meta_data("_ds_pos_modified_{$item_id}_subtotal");
            $order->delete_meta_data("_ds_pos_modified_{$item_id}_total");
        }
    }
    
    if ($needs_save) {
        // Force save the items
        foreach ($order->get_items() as $item) {
            $item->save();
        }
        error_log("DS POS: FORCE saved all modified membership items with modified pricing");
    }
}

// Helper function to check if this is a DS POS order (reuse from trial hook)
if (!function_exists('is_ds_pos_order')) {
    function is_ds_pos_order($order) {
        if (!$order) {
            return false;
        }
        
        // Check multiple indicators that this is a DS POS order
        $created_via = $order->get_created_via();
        $source = $order->get_meta('source');
        $order_source = $order->get_meta('_order_source');
        $pos_order_id = $order->get_meta('_pos_order_id');
        
        return (
            $created_via === 'DS POS' ||
            $source === 'DS POS' ||
            $order_source === 'DS POS' ||
            !empty($pos_order_id)
        );
    }
}

// Helper function to check if item has modified pricing (ANY product, not just memberships)
function has_modified_pricing($item) {
    if (!$item) {
        return false;
    }
    
    // Skip trial memberships (they have their own handler)
    if ($item->get_meta('_trial_membership') === 'true') {
        return false;
    }
    
    // Check if we have pricing modification metadata for ANY product
    $has_original_price = $item->get_meta('_ds_pos_original_price') !== '';
    $has_modified_price = $item->get_meta('_ds_pos_modified_price') !== '';
    $has_modified_pricing_flag = $item->get_meta('_ds_pos_modified_pricing') === 'true';
    
    // Debug logging
    error_log("DS POS: Checking modified pricing for item: " . $item->get_name());
    error_log("DS POS: - has_original_price: " . ($has_original_price ? 'yes' : 'no'));
    error_log("DS POS: - has_modified_price: " . ($has_modified_price ? 'yes' : 'no'));  
    error_log("DS POS: - has_modified_pricing_flag: " . ($has_modified_pricing_flag ? 'yes' : 'no'));
    
    if ($has_original_price) {
        error_log("DS POS: - original_price: " . $item->get_meta('_ds_pos_original_price'));
        error_log("DS POS: - modified_price: " . $item->get_meta('_ds_pos_modified_price'));
    }
    
    // Item has modified pricing if we have the flag OR both original and modified prices
    return $has_modified_pricing_flag || ($has_original_price && $has_modified_price);
}

// Helper function to get the modified price (any product) - returns LINE TOTAL
function get_modified_price($item) {
    if (!$item) {
        return false;
    }
    
    $quantity = $item->get_quantity();
    
    // Try to get modified price directly (per-unit)
    $modified_price = $item->get_meta('_ds_pos_modified_price');
    if ($modified_price !== '' && is_numeric($modified_price)) {
        $per_unit_price = round(floatval($modified_price), 2);
        return $per_unit_price * $quantity; // Return line total
    }
    
    // Calculate from original price and discount (per-unit)
    $original_price = $item->get_meta('_ds_pos_original_price');
    $discount_amount = $item->get_meta('_ds_pos_discount_amount');
    
    if ($original_price !== '' && $discount_amount !== '' && 
        is_numeric($original_price) && is_numeric($discount_amount)) {
        $final_price_per_unit = floatval($original_price) - floatval($discount_amount);
        $final_price_per_unit = max(0, $final_price_per_unit); // Ensure non-negative
        $final_price_per_unit = round($final_price_per_unit, 2); // Round to 2 decimal places
        return $final_price_per_unit * $quantity; // Return line total
    }
    
    return false;
}

// NUCLEAR OPTION: Hook into order display/rendering for modified memberships
add_filter('woocommerce_order_get_items', 'force_ds_pos_modified_membership_display', 10, 2);

function force_ds_pos_modified_membership_display($items, $order) {
    if (!is_ds_pos_order($order)) {
        return $items;
    }
    
    // 🔥 ITEM-LEVEL DISCOUNT FIX: Set subtotal to ORIGINAL price, total to MODIFIED price
    foreach ($items as $item_id => $item) {
        if (has_modified_pricing($item)) {
            $original_price = $item->get_meta('_ds_pos_original_price');
            $modified_price = get_modified_price($item);
            
            if ($original_price && $modified_price !== false) {
                $original_subtotal = floatval($original_price) * $item->get_quantity();
                $modified_total = round($modified_price, 2);
                
                // Set subtotal to ORIGINAL price (shows in PRICE column)
                $item->set_subtotal($original_subtotal);
                // Set total to MODIFIED price (shows in SUBTOTAL column)
                $item->set_total($modified_total);
                
                error_log("DS POS: NUCLEAR - Force display pricing {$item_id}: subtotal=$" . $original_subtotal . ", total=$" . $modified_total);
            }
        }
    }
    
    return $items;
}

// Hook into line item pricing display for modified memberships
add_filter('woocommerce_order_item_get_subtotal', 'force_ds_pos_modified_membership_line_item_subtotal', 10, 2);
add_filter('woocommerce_order_item_get_total', 'force_ds_pos_modified_membership_line_item_total', 10, 2);

function force_ds_pos_modified_membership_line_item_subtotal($subtotal, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $subtotal;
    }
    
    // 🔥 ITEM-LEVEL DISCOUNT FIX: Return ORIGINAL price for subtotal column
    if (has_modified_pricing($item)) {
        $original_price = $item->get_meta('_ds_pos_original_price');
        if ($original_price) {
            $display_subtotal = floatval($original_price) * $item->get_quantity();
            error_log("DS POS: NUCLEAR - Displaying ORIGINAL price in subtotal: $" . $display_subtotal);
            return $display_subtotal;
        }
        
        // Fallback to modified price if no original price stored
        $modified_price = get_modified_price($item);
        if ($modified_price !== false) {
            $rounded_price = round($modified_price, 2);
            error_log("DS POS: NUCLEAR - Force modified pricing line item subtotal to $" . $rounded_price);
            return $rounded_price;
        }
    }
    
    return $subtotal;
}

function force_ds_pos_modified_membership_line_item_total($total, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $total;
    }
    
    if (has_modified_pricing($item)) {
        $modified_price = get_modified_price($item);
        if ($modified_price !== false) {
            $rounded_price = round($modified_price, 2);
            error_log("DS POS: NUCLEAR - Force modified pricing line item total to $" . $rounded_price);
            return $rounded_price;
        }
    }
    
    return $total;
}

// Hook into product price calculation for modified membership items
add_filter('woocommerce_product_get_price', 'override_modified_membership_item_price', 10, 2);
add_filter('woocommerce_product_variation_get_price', 'override_modified_membership_item_price', 10, 2);

function override_modified_membership_item_price($price, $product) {
    // Only override during order creation/calculation
    if (!doing_action('woocommerce_rest_insert_shop_order_object') && 
        !doing_action('woocommerce_checkout_create_order_line_item')) {
        return $price;
    }
    
    // Check if we're in a DS POS modified membership context
    global $ds_pos_modified_membership_context;
    if (!$ds_pos_modified_membership_context) {
        return $price;
    }
    
    // Return modified price if this product is marked for price modification
    if (isset($ds_pos_modified_membership_context['product_' . $product->get_id()])) {
        $modified_price = $ds_pos_modified_membership_context['product_' . $product->get_id()];
        error_log("DS POS: Override product price for " . $product->get_id() . " to $" . $modified_price);
        return $modified_price;
    }
    
    return $price;
}

// Hook into order total calculation to ensure modified memberships are calculated correctly
add_filter('woocommerce_order_get_total', 'adjust_ds_pos_modified_membership_total', 10, 2);

function adjust_ds_pos_modified_membership_total($total, $order) {
    if (!is_ds_pos_order($order)) {
        return $total;
    }
    
    // Check if we have modified memberships and adjust total if needed
    $modified_membership_adjustment = 0;
    foreach ($order->get_items() as $item) {
        if (has_modified_pricing($item)) {
            $modified_price = get_modified_price($item);
            $current_item_total = $item->get_total();
            
            if ($modified_price !== false && $current_item_total != $modified_price) {
                $adjustment = $modified_price - $current_item_total;
                $modified_membership_adjustment += $adjustment;
                error_log("DS POS: Adjusting order total by $" . $adjustment . " for modified membership: " . $item->get_name() . " (current: $" . $current_item_total . " -> modified: $" . $modified_price . ")");
            }
        }
    }
    
    if ($modified_membership_adjustment != 0) {
        $adjusted_total = $total + $modified_membership_adjustment;
        $rounded_total = round(max(0, $adjusted_total), 2); // Ensure total never goes negative and round to 2 decimal places
        error_log("DS POS: NUCLEAR - Adjusted order total from $" . $total . " to $" . $rounded_total . " (adjustment: $" . $modified_membership_adjustment . ")");
        return $rounded_total;
    }
    
    return $total;
}

// Add admin notice for successful installation
add_action('admin_notices', 'ds_pos_modified_membership_pricing_notice');

function ds_pos_modified_membership_pricing_notice() {
    if (current_user_can('manage_options')) {
        echo '<div class="notice notice-success"><p><strong>DS POS Modified Product Pricing:</strong> Custom modified pricing hooks are active and will preserve discounted/modified pricing for ALL products (not just memberships).</p></div>';
    }
}

// Advanced: Hook into WooCommerce Subscriptions to ensure modified memberships create proper subscriptions
add_filter('woocommerce_subscriptions_product_sign_up_fee', 'force_modified_membership_signup_fee', 10, 2);

function force_modified_membership_signup_fee($sign_up_fee, $product) {
    // Check if we're processing a DS POS modified membership
    global $ds_pos_modified_membership_context;
    if ($ds_pos_modified_membership_context && 
        isset($ds_pos_modified_membership_context['product_' . $product->get_id()])) {
        $modified_price = $ds_pos_modified_membership_context['product_' . $product->get_id()];
        error_log("DS POS: Setting modified membership signup fee to $" . $modified_price . " for product " . $product->get_id());
        return $modified_price;
    }
    
    return $sign_up_fee;
}

error_log("DS POS Modified Membership Pricing hooks loaded successfully");
///////////


// ========================================
// SAFE COMPREHENSIVE WOOCOMMERCE CACHE CLEARING
// ========================================

// 1. Clear ALL caches when variation is updated in admin
add_action('woocommerce_update_product_variation', function($variation_id) {
    $variation = new WC_Product_Variation($variation_id);
    $parent_id = $variation->get_parent_id();
    
    // Clear WooCommerce transients (SAFE - cache only)
    wc_delete_product_transients($parent_id);
    wc_delete_product_transients($variation_id);
    
    // Clear WordPress post cache (SAFE - cache only)
    clean_post_cache($parent_id);
    clean_post_cache($variation_id);
    
    // Clear object cache (SAFE - cache only)
    wp_cache_delete('wc_product_' . $parent_id, 'products');
    wp_cache_delete('wc_product_' . $variation_id, 'products');
    wp_cache_delete('wc_var_' . $variation_id, 'products');
    
    // Clear variation cache specifically (SAFE - cache only)
    wp_cache_delete('wc_product_children_' . $parent_id, 'products');
    
    // Force regenerate cache version (SAFE - cache only)
    WC_Cache_Helper::get_transient_version('product', true);
    
    error_log("CACHE CLEARED: Variation $variation_id updated, all caches cleared");
}, 10, 1);

// 2. Clear ALL caches when parent variable product is updated
add_action('woocommerce_update_product', function($product_id) {
    $product = wc_get_product($product_id);
    if ($product && $product->is_type('variable')) {
        
        // Clear parent product caches (SAFE - cache only)
        wc_delete_product_transients($product_id);
        clean_post_cache($product_id);
        wp_cache_delete('wc_product_' . $product_id, 'products');
        
        // Clear ALL variation caches (SAFE - cache only)
        $variation_ids = $product->get_children();
        foreach ($variation_ids as $variation_id) {
            wc_delete_product_transients($variation_id);
            clean_post_cache($variation_id);
            wp_cache_delete('wc_product_' . $variation_id, 'products');
            wp_cache_delete('wc_var_' . $variation_id, 'products');
        }
        
        // Clear variation collection cache (SAFE - cache only)
        wp_cache_delete('wc_product_children_' . $product_id, 'products');
        
        error_log("CACHE CLEARED: Variable product $product_id updated, all variation caches cleared");
    }
}, 10, 1);

// 3. Force fresh data for ALL REST API calls
add_action('woocommerce_rest_prepare_product_variation', function($response, $object, $request) {
    $variation_id = $object->get_id();
    $parent_id = $object->get_parent_id();
    
    // Nuclear cache clearing before REST response (SAFE - cache only)
    wc_delete_product_transients($parent_id);
    wc_delete_product_transients($variation_id);
    clean_post_cache($parent_id);
    clean_post_cache($variation_id);
    
    // Clear ALL possible object caches (SAFE - cache only)
    wp_cache_delete('wc_product_' . $parent_id, 'products');
    wp_cache_delete('wc_product_' . $variation_id, 'products');
    wp_cache_delete('wc_var_' . $variation_id, 'products');
    wp_cache_delete('wc_product_children_' . $parent_id, 'products');
    
    error_log("REST API: Fresh variation data forced for variation $variation_id");
    
    return $response;
}, 5, 3);

// 4. Also clear for parent product REST calls
add_action('woocommerce_rest_prepare_product_object', function($response, $object, $request) {
    $product_id = $object->get_id();
    
    if ($object->is_type('variable')) {
        // Nuclear cache clearing for variable products (SAFE - cache only)
        wc_delete_product_transients($product_id);
        clean_post_cache($product_id);
        wp_cache_delete('wc_product_' . $product_id, 'products');
        wp_cache_delete('wc_product_children_' . $product_id, 'products');
        
        // Clear all variation caches too (SAFE - cache only)
        $variation_ids = $object->get_children();
        foreach ($variation_ids as $variation_id) {
            wc_delete_product_transients($variation_id);
            clean_post_cache($variation_id);
            wp_cache_delete('wc_product_' . $variation_id, 'products');
            wp_cache_delete('wc_var_' . $variation_id, 'products');
        }
        
        error_log("REST API: Fresh variable product data forced for product $product_id");
    }
    
    return $response;
}, 5, 3);

// 5. Disable object caching for variation queries entirely
add_filter('woocommerce_rest_product_variation_object_query', function($args, $request) {
    $args['cache_results'] = false;
    $args['update_post_meta_cache'] = false;
    $args['update_post_term_cache'] = false;
    return $args;
}, 10, 2);

// 6. Add cache-busting headers for REST API responses
add_action('rest_api_init', function() {
    add_filter('rest_post_dispatch', function($response, $server, $request) {
        if (strpos($request->get_route(), '/wc/v3/products') !== false) {
            $response->header('Cache-Control', 'no-cache, no-store, must-revalidate, max-age=0');
            $response->header('Pragma', 'no-cache');
            $response->header('Expires', '0');
        }
        return $response;
    }, 10, 3);
});
/////




/**
 * DS POS Fulfillment Location - WordPress Snippet
 * 
 * Stock deduction is now handled UPSTREAM by Django via ATUM's native mi_inventories
 * field on line items during order creation. This snippet only needs to:
 * 
 * 1. Preserve _fulfillment_location meta on order items (for display/audit)
 * 2. Act as a FALLBACK filter if mi_inventories wasn't sent by Django
 *    (e.g., if ATUM inventory lookup failed on the Django side)
 */

if (!function_exists('ds_pos_check_order_source')) {
    function ds_pos_check_order_source($order) {
        if (!$order) {
            return false;
        }
        $customer_note = $order->get_customer_note();
        return strpos($customer_note, 'POS Order:') !== false;
    }
}

if (!function_exists('ds_pos_get_atum_inventory_id')) {
    function ds_pos_get_atum_inventory_id($location_name, $product_id = null) {
        global $wpdb;
        
        $result = null;
        
        // Try to match by inventory name + product_id
        if ($product_id) {
            $result = $wpdb->get_var($wpdb->prepare(
                "SELECT id FROM {$wpdb->prefix}atum_inventories WHERE product_id = %d AND name = %s LIMIT 1",
                $product_id, $location_name
            ));
        }
        
        // Fallback: match by location taxonomy
        if (empty($result) && $product_id) {
            $location_slug = sanitize_title($location_name);
            $result = $wpdb->get_var($wpdb->prepare(
                "SELECT ai.id 
                 FROM {$wpdb->prefix}atum_inventories ai
                 INNER JOIN {$wpdb->prefix}atum_inventory_locations ail ON ai.id = ail.inventory_id
                 INNER JOIN {$wpdb->prefix}terms t ON ail.location_id = t.term_id
                 WHERE ai.product_id = %d AND (t.slug = %s OR t.name = %s)
                 LIMIT 1",
                $product_id, $location_slug, $location_name
            ));
        }
        
        // Last fallback: any inventory with this name
        if (empty($result)) {
            $result = $wpdb->get_var($wpdb->prepare(
                "SELECT id FROM {$wpdb->prefix}atum_inventories WHERE name = %s LIMIT 1",
                $location_name
            ));
        }
        
        return $result;
    }
}

// STEP 1: Preserve fulfillment location meta on order creation
add_action('woocommerce_rest_insert_shop_order_object', function ($order, $request, $creating) {
    if (!$creating || !ds_pos_check_order_source($order)) {
        return;
    }

    error_log("DS POS MI: Processing POS order #" . $order->get_id());

    foreach ($order->get_items() as $item_id => $item) {
        $fulfillment_location = $item->get_meta('_fulfillment_location');
        if (empty($fulfillment_location)) {
            continue;
        }

        // Store fulfillment location in order-level meta for audit trail
        $order->add_meta_data("_ds_pos_fulfillment_{$item_id}", $fulfillment_location, true);
        
        // Re-save to ensure it persists
        $item->update_meta_data('_fulfillment_location', $fulfillment_location);
        $item->save_meta_data();

        error_log("DS POS MI: Preserved fulfillment location '{$fulfillment_location}' for item '{$item->get_name()}' (item_id={$item_id})");
    }

    $order->save();
}, 10, 3);

// STEP 2: FALLBACK - Force ATUM to use POS-chosen inventory if mi_inventories
//         was not sent by Django (e.g., ATUM API lookup failed on Django side)
add_filter('atum/multi_inventory/order_item_inventories', function ($inventories, $item) {
    $fulfillment_location = $item->get_meta('_fulfillment_location');
    if (empty($fulfillment_location)) {
        return $inventories;
    }

    $product_id = $item->get_variation_id() ?: $item->get_product_id();
    $atum_inventory_id = ds_pos_get_atum_inventory_id($fulfillment_location, $product_id);

    if (empty($atum_inventory_id)) {
        error_log("DS POS MI FALLBACK: Could not resolve inventory for '{$fulfillment_location}' product {$product_id}, using ATUM default");
        return $inventories;
    }

    foreach ($inventories as $inventory) {
        if ((int) $inventory->id === (int) $atum_inventory_id) {
            error_log("DS POS MI FALLBACK: Forcing inventory ID {$atum_inventory_id} ('{$inventory->name}') for '{$item->get_name()}'");
            return [$inventory];
        }
    }

    if (class_exists('\AtumMultiInventory\Inc\Helpers')) {
        try {
            $inventory = \AtumMultiInventory\Inc\Helpers::get_inventory($atum_inventory_id, 0, false, true);
            if ($inventory && $inventory->id) {
                error_log("DS POS MI FALLBACK: Loaded inventory ID {$atum_inventory_id} directly for '{$item->get_name()}'");
                return [$inventory];
            }
        } catch (\Exception $e) {
            error_log("DS POS MI FALLBACK: Error loading inventory {$atum_inventory_id}: " . $e->getMessage());
        }
    }

    error_log("DS POS MI FALLBACK: Inventory ID {$atum_inventory_id} not found, using ATUM default for '{$item->get_name()}'");
    return $inventories;
}, 10, 2);

error_log("DS POS Fulfillment Location hooks loaded (mi_inventories upstream + fallback filter)");

/**
 * Hide unnecessary order item meta from WooCommerce admin
 */

add_filter('woocommerce_hidden_order_itemmeta', function($hidden_meta_keys) {
    $hidden_meta_keys = array_merge($hidden_meta_keys, [
        '_atum_location',
        '_atum_manage_stock',
        '_atum_inventory_id',
        '_ds_pos_original_price',
        '_ds_pos_modified_price',
        '_ds_pos_discount_amount',
        '_ds_pos_modified_pricing',
        '_ds_pos_is_subscription',
        '_ds_pos_subscription_already_applied',
        '_ds_pos_subscription_discount_percentage_applied',
        '_ds_pos_regular_product_price',
        '_fulfillment_location',
        '_ds_pos_locked_subtotal',
        '_ds_pos_locked_total',
        '_ds_pos_pricing_locked',
        '_ds_bundle_parent_id',
        '_ds_bundle_child_item',
        '_ds_bundle_parent_item',
        '_ds_line_subtotal',
        '_ds_line_total',
        'Bundle Type',
        'Bundle ID',
        'Original Bundle Price',
    ]);
    return $hidden_meta_keys;
});



/**
 * DS POS Pricing Lock - Prevent WooCommerce from modifying DS POS order prices
 * 
 * This is the DEFINITIVE solution to prevent WooCommerce from applying
 * automatic membership discounts or any other price modifications to DS POS orders.
 */

// PRIORITY 1: Lock DS POS prices immediately when order is created
add_action('woocommerce_rest_insert_shop_order_object', 'lock_ds_pos_order_pricing', 1, 3);

function lock_ds_pos_order_pricing($order, $request, $creating) {
    if (!$creating || !is_ds_pos_order($order)) {
        return;
    }
    
    error_log("DS POS PRICING LOCK: Locking prices for order " . $order->get_id());
    
    // Store original DS POS prices before WooCommerce can modify them
    foreach ($order->get_items() as $item_id => $item) {
        $original_subtotal = $item->get_subtotal();
        $original_total = $item->get_total();
        
        // Store the DS POS prices as meta data
        $item->add_meta_data('_ds_pos_locked_subtotal', $original_subtotal, true);
        $item->add_meta_data('_ds_pos_locked_total', $original_total, true);
        $item->add_meta_data('_ds_pos_pricing_locked', 'true', true);
        $item->save();
        
        error_log("DS POS PRICING LOCK: Stored original prices for '{$item->get_name()}': subtotal=$" . $original_subtotal . ", total=$" . $original_total);
    }
    
    // Calculate and store the correct order total from line items
    $locked_order_total = 0;
    foreach ($order->get_items() as $item) {
        $locked_order_total += floatval($item->get_total());
    }
    // Include fee lines (e.g., discounts)
    foreach ($order->get_fees() as $fee) {
        $locked_order_total += floatval($fee->get_total());
    }
    
    // Mark order as having locked pricing and store the correct total
    $order->add_meta_data('_ds_pos_pricing_locked', 'true', true);
    $order->add_meta_data('_ds_pos_locked_order_total', $locked_order_total, true);
    $order->save();
    
    error_log("DS POS PRICING LOCK: Stored locked order total: \$" . $locked_order_total);
}

// PRIORITY 2: Prevent membership discounts during order processing
add_filter('woocommerce_order_item_product', 'prevent_ds_pos_membership_discount', 1, 2);

function prevent_ds_pos_membership_discount($product, $item) {
    // Protect against deleted/invalid products
    if (!$product || !is_object($product)) {
        return $product;
    }
    
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $product;
    }
    
    // Create a proxy product that returns DS POS prices
    if ($item->get_meta('_ds_pos_pricing_locked') === 'true') {
        $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
        if ($locked_subtotal) {
            $ds_pos_price = floatval($locked_subtotal) / $item->get_quantity();
            
            // Override product price methods to return DS POS price
            add_filter('woocommerce_product_get_price', function($price, $prod) use ($product, $ds_pos_price) {
                if ($prod->get_id() === $product->get_id()) {
                    return $ds_pos_price;
                }
                return $price;
            }, 999, 2);
            
            add_filter('woocommerce_product_variation_get_price', function($price, $prod) use ($product, $ds_pos_price) {
                if ($prod->get_id() === $product->get_id()) {
                    return $ds_pos_price;
                }
                return $price;
            }, 999, 2);
            
            error_log("DS POS PRICING LOCK: Overriding product price for '{$product->get_name()}' to $" . $ds_pos_price);
        }
    }
    
    return $product;
}

// PRIORITY 3: Force DS POS prices during any calculation
add_action('woocommerce_order_before_calculate_totals', 'force_ds_pos_pricing', 1, 2);

function force_ds_pos_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order) || $order->get_meta('_ds_pos_pricing_locked') !== 'true') {
        return;
    }
    
    error_log("DS POS PRICING LOCK: Forcing DS POS prices during calculation for order " . $order->get_id());
    
    foreach ($order->get_items() as $item_id => $item) {
        $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
        $locked_total = $item->get_meta('_ds_pos_locked_total');
        
        if ($locked_subtotal && $locked_total) {
            $item->set_subtotal(floatval($locked_subtotal));
            $item->set_total(floatval($locked_total));
            error_log("DS POS PRICING LOCK: Restored locked prices for '{$item->get_name()}': $" . $locked_subtotal);
        }
    }
}

// PRIORITY 4: Final enforcement after calculation
add_action('woocommerce_order_after_calculate_totals', 'enforce_ds_pos_pricing', 999, 2);

function enforce_ds_pos_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order) || $order->get_meta('_ds_pos_pricing_locked') !== 'true') {
        return;
    }
    
    error_log("DS POS PRICING LOCK: Final enforcement of DS POS prices for order " . $order->get_id());
    
    $needs_save = false;
    foreach ($order->get_items() as $item_id => $item) {
        $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
        $locked_total = $item->get_meta('_ds_pos_locked_total');
        
        if ($locked_subtotal && $locked_total) {
            $current_subtotal = $item->get_subtotal();
            $current_total = $item->get_total();
            
            // Check if WooCommerce changed the prices
            if (abs($current_subtotal - floatval($locked_subtotal)) > 0.01 || 
                abs($current_total - floatval($locked_total)) > 0.01) {
                
                $item->set_subtotal(floatval($locked_subtotal));
                $item->set_total(floatval($locked_total));
                $needs_save = true;
                
                error_log("DS POS PRICING LOCK: CORRECTED pricing for '{$item->get_name()}': " . 
                         "WC tried to change $" . $current_subtotal . " to $" . $locked_subtotal);
            }
        }
    }
    
    if ($needs_save) {
        foreach ($order->get_items() as $item) {
            $item->save();
        }
        error_log("DS POS PRICING LOCK: Saved corrected prices");
    }
    
    // Restore the locked order total to prevent WC Product Bundles or other plugins
    // from overriding it during calculate_totals()
    $locked_order_total = $order->get_meta('_ds_pos_locked_order_total');
    if ($locked_order_total !== '' && $locked_order_total !== false) {
        $current_total = floatval($order->get_total());
        $locked_total_float = floatval($locked_order_total);
        if (abs($current_total - $locked_total_float) > 0.01) {
            $order->set_total($locked_total_float);
            error_log("DS POS PRICING LOCK: CORRECTED order total from \$" . $current_total . " to \$" . $locked_total_float);
        }
    }
}

// NUCLEAR OPTION: Override all price getters for DS POS orders
add_filter('woocommerce_order_item_get_subtotal', 'force_ds_pos_item_subtotal', 999, 2);
add_filter('woocommerce_order_item_get_total', 'force_ds_pos_item_total', 999, 2);

function force_ds_pos_item_subtotal($subtotal, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $subtotal;
    }
    
    error_log("🔥 DS POS SUBTOTAL HOOK CALLED - Item: " . $item->get_name() . ", Current subtotal: $" . $subtotal);
    
    // 🔥 ITEM-LEVEL DISCOUNT FIX: Display original price in subtotal column
    $original_price = $item->get_meta('_ds_pos_original_price');
    $modified_price = $item->get_meta('_ds_pos_modified_price');
    
    error_log("🔥 DS POS METADATA - Original: $" . $original_price . ", Modified: $" . $modified_price);
    
    if ($original_price) {
        $display_subtotal = floatval($original_price) * $item->get_quantity();
        error_log("🔥 DS POS: Displaying original price in subtotal: $" . $display_subtotal);
        return $display_subtotal;
    }
    
    $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
    if ($locked_subtotal && $order->get_meta('_ds_pos_pricing_locked') === 'true') {
        return floatval($locked_subtotal);
    }
    
    error_log("🔥 DS POS: No original price found, returning default subtotal: $" . $subtotal);
    return $subtotal;
}

function force_ds_pos_item_total($total, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $total;
    }
    
    $locked_total = $item->get_meta('_ds_pos_locked_total');
    if ($locked_total && $order->get_meta('_ds_pos_pricing_locked') === 'true') {
        return floatval($locked_total);
    }
    
    return $total;
}

// Helper function to check if this is a DS POS order
if (!function_exists('is_ds_pos_order')) {
    function is_ds_pos_order($order) {
        if (!$order) {
            return false;
        }
        
        // Check multiple indicators that this is a DS POS order
        $created_via = $order->get_created_via();
        $source = $order->get_meta('source');
        $order_source = $order->get_meta('_order_source');
        $pos_order_id = $order->get_meta('_pos_order_id');
        
        return (
            $created_via === 'DS POS' ||
            $source === 'DS POS' ||
            $order_source === 'DS POS' ||
            !empty($pos_order_id)
        );
    }
}

error_log("DS POS PRICING LOCK: Comprehensive pricing protection loaded successfully");

// ============================================================================
// ORDER ORIGIN DISPLAY - Show "DS POS {Location}" in WooCommerce Orders List
// ============================================================================

/**
 * Debug: Log all columns to find the actual Origin column key
 * This helps identify which plugin is creating the Origin column
 */
add_filter('manage_edit-shop_order_columns', function($columns) {
    error_log("DS POS ORIGIN DEBUG: Available order columns: " . print_r(array_keys($columns), true));
    return $columns;
}, 1); // Priority 1 to run first and see original columns

/**
 * Start output buffering BEFORE any column rendering to capture plugin output
 * Priority 1 ensures we run before any plugin
 */
add_action('manage_shop_order_posts_custom_column', function($column, $post_id) {
    $possible_origin_keys = array('origin', 'order_origin', '_origin', 'source', 'order_source');
    if (in_array($column, $possible_origin_keys)) {
        ob_start();
    }
}, 1, 2);

add_action('manage_woocommerce_page_wc-orders_custom_column', function($column, $post_id) {
    $possible_origin_keys = array('origin', 'order_origin', '_origin', 'source', 'order_source');
    if (in_array($column, $possible_origin_keys)) {
        ob_start();
    }
}, 1, 2);

/**
 * AGGRESSIVE APPROACH: Hook into multiple possible column keys
 * This covers all possible Origin column implementations
 * Priority 9999 ensures we run after any plugin and discard their output
 */
function ds_pos_display_origin_column($column, $post_id) {
    // List of possible column keys used by different plugins
    $possible_origin_keys = array(
        'origin',           // Generic
        'order_origin',     // Some plugins
        '_origin',          // Some plugins
        'source',           // Some plugins
        'order_source',     // Some plugins
    );
    
    // Check if this is an origin-related column
    if (!in_array($column, $possible_origin_keys)) {
        return;
    }
    
    error_log("DS POS ORIGIN DEBUG - Order #$post_id: Detected origin column key: $column");
    
    // Get and discard any buffered output from plugins
    $plugin_output = @ob_get_clean();
    if ($plugin_output) {
        error_log("DS POS ORIGIN DEBUG - Order #$post_id: Discarded plugin output: " . substr($plugin_output, 0, 100));
    }
    
    // Try WooCommerce HPOS first (newer method)
    $order = wc_get_order($post_id);
    $origin = '';
    
    if ($order) {
        // Check HPOS compatibility
        $is_hpos = $order->get_meta('_order_origin', true);
        error_log("DS POS ORIGIN DEBUG - Order #$post_id: Is HPOS? " . ($is_hpos ? 'Yes' : 'No'));
        
        // Try to get _order_origin from order meta
        $origin = $order->get_meta('_order_origin', true);
        error_log("DS POS ORIGIN DEBUG - Order #$post_id: HPOS _order_origin = " . var_export($origin, true));
    }
    
    // Fallback to legacy post meta if HPOS didn't work
    if (empty($origin)) {
        $origin = get_post_meta($post_id, '_order_origin', true);
        error_log("DS POS ORIGIN DEBUG - Order #$post_id: Legacy _order_origin = " . var_export($origin, true));
    }
    
    // If no _order_origin, check for other origin indicators
    if (empty($origin)) {
        if ($order) {
            $created_via = $order->get_created_via();
            $order_source = $order->get_meta('_order_source', true);
            $pos_order_id = $order->get_meta('_pos_order_id', true);
        } else {
            $created_via = get_post_meta($post_id, '_created_via', true);
            $order_source = get_post_meta($post_id, '_order_source', true);
            $pos_order_id = get_post_meta($post_id, '_pos_order_id', true);
        }
        
        error_log("DS POS ORIGIN DEBUG - Order #$post_id: created_via = " . var_export($created_via, true));
        error_log("DS POS ORIGIN DEBUG - Order #$post_id: _order_source = " . var_export($order_source, true));
        error_log("DS POS ORIGIN DEBUG - Order #$post_id: _pos_order_id = " . var_export($pos_order_id, true));
        
        // If it's a DS POS order, try to construct origin from location data
        if ($created_via === 'DS POS' || $order_source === 'DS POS' || !empty($pos_order_id)) {
            // Try to get location name
            if ($order) {
                $pos_location_name = $order->get_meta('_pos_location_name', true);
                $assigned_location = $order->get_meta('_assigned_location', true);
            } else {
                $pos_location_name = get_post_meta($post_id, '_pos_location_name', true);
                $assigned_location = get_post_meta($post_id, '_assigned_location', true);
            }
            
            error_log("DS POS ORIGIN DEBUG - Order #$post_id: _pos_location_name = " . var_export($pos_location_name, true));
            error_log("DS POS ORIGIN DEBUG - Order #$post_id: _assigned_location = " . var_export($assigned_location, true));
            
            if (!empty($pos_location_name)) {
                $origin = 'DS POS ' . $pos_location_name;
                error_log("DS POS ORIGIN DEBUG - Order #$post_id: Set origin from pos_location_name: $origin");
            } elseif (!empty($assigned_location)) {
                // Extract first word from assigned location
                $location_parts = explode(' ', trim($assigned_location));
                $location_name = $location_parts[0];
                $origin = 'DS POS ' . $location_name;
                error_log("DS POS ORIGIN DEBUG - Order #$post_id: Set origin from assigned_location: $origin");
            } else {
                $origin = 'DS POS';
                error_log("DS POS ORIGIN DEBUG - Order #$post_id: No location found, using default: $origin");
            }
        } else {
            // Check if created_via has a value we can use
            if (!empty($created_via) && $created_via !== 'checkout') {
                $origin = ucwords(str_replace(['_', '-'], ' ', $created_via));
                error_log("DS POS ORIGIN DEBUG - Order #$post_id: Using created_via: $origin");
            }
        }
    }
    
    // Display the origin with appropriate styling
    if (!empty($origin)) {
        // Check if it's a DS POS order for color coding
        if (stripos($origin, 'DS POS') !== false) {
            // Blue color for DS POS orders
            echo '<span style="color: #2271b1; font-weight: 500;">' . esc_html($origin) . '</span>';
            error_log("DS POS ORIGIN DEBUG - Order #$post_id: ✅ DISPLAYING DS POS origin: $origin");
        } else {
            // Different color for other origins (like "Web admin")
            echo '<span style="color: #059669; font-weight: 500;">' . esc_html($origin) . '</span>';
            error_log("DS POS ORIGIN DEBUG - Order #$post_id: ✅ DISPLAYING other origin: $origin");
        }
    } else {
        // Show "Unknown" in gray if no origin is set
        echo '<span style="color: #999; font-style: italic;">Unknown</span>';
        error_log("DS POS ORIGIN DEBUG - Order #$post_id: ⚠️ No origin found, displaying Unknown");
    }
}

// Hook into manage_shop_order_posts_custom_column with priority 9999
add_action('manage_shop_order_posts_custom_column', 'ds_pos_display_origin_column', 9999, 2);

// Also try HPOS-specific action if it exists
add_action('manage_woocommerce_page_wc-orders_custom_column', 'ds_pos_display_origin_column', 9999, 2);

/**
 * Make the "Origin" column sortable in the orders list
 * Allows sorting orders by their origin
 * 
 * @param array $columns Sortable columns
 * @return array Modified sortable columns
 * @since 1.0.0
 */
add_filter('manage_edit-shop_order_sortable_columns', function($columns) {
    $columns['origin'] = 'origin';
    return $columns;
});

/**
 * Handle the sorting logic for the "Origin" column
 * Sorts by the _order_origin meta value
 * 
 * @param WP_Query $query The WordPress query
 * @since 1.0.0
 */
add_action('pre_get_posts', function($query) {
    if (!is_admin() || !$query->is_main_query()) {
        return;
    }
    
    if ($query->get('orderby') === 'origin') {
        $query->set('meta_key', '_order_origin');
        $query->set('orderby', 'meta_value');
    }
});

/**
 * Add CSS to hide duplicate "Unknown" text in Origin column
 * This provides a visual backup in case JavaScript doesn't run
 * 
 * @since 1.0.0
 */
add_action('admin_head', function() {
    global $pagenow, $typenow;
    
    // Only run on WooCommerce orders page
    if (($pagenow === 'edit.php' && $typenow === 'shop_order') || 
        ($pagenow === 'admin.php' && isset($_GET['page']) && $_GET['page'] === 'wc-orders')) {
        ?>
        <style type="text/css">
            /* Hide standalone "Unknown" spans in origin column */
            .column-origin span[style*="color:#999"],
            .column-origin span[style*="color: #999"],
            td[data-colname="Origin"] span[style*="color:#999"],
            td[data-colname="Origin"] span[style*="color: #999"] {
                /* Don't hide if it's the only content */
            }
        </style>
        <?php
    }
});

/**
 * Clean up duplicate "Unknown" text in Origin column using JavaScript
 * This removes any "Unknown" text that appears before our custom origin
 * 
 * @since 1.0.0
 */
add_action('admin_footer', function() {
    global $pagenow, $typenow;
    
    // Only run on WooCommerce orders page
    if (($pagenow === 'edit.php' && $typenow === 'shop_order') || 
        ($pagenow === 'admin.php' && isset($_GET['page']) && $_GET['page'] === 'wc-orders')) {
        ?>
        <script type="text/javascript">
        jQuery(document).ready(function($) {
            // Function to clean up origin column
            function cleanOriginColumn() {
                // Find all cells in the origin column
                $('.column-origin, td[data-colname="Origin"]').each(function() {
                    var $cell = $(this);
                    var html = $cell.html();
                    
                    // If cell contains both "Unknown" and something else (DS POS, Web admin, etc.)
                    if (html.indexOf('Unknown') !== -1 && html.length > 50) {
                        // Remove "Unknown" text and its span
                        html = html.replace(/<span[^>]*>Unknown<\/span>/gi, '');
                        html = html.replace(/Unknown/gi, '');
                        
                        // Clean up any extra whitespace
                        html = html.trim();
                        
                        // Update the cell only if there's still content left
                        if (html.length > 0) {
                            $cell.html(html);
                            console.log('DS POS ORIGIN: Cleaned duplicate Unknown text');
                        }
                    }
                });
            }
            
            // Run immediately
            cleanOriginColumn();
            
            // Run again after a short delay (in case content loads dynamically)
            setTimeout(cleanOriginColumn, 500);
            setTimeout(cleanOriginColumn, 1000);
            
            // Run when AJAX requests complete (for pagination, filtering, etc.)
            $(document).ajaxComplete(function() {
                setTimeout(cleanOriginColumn, 300);
            });
            
            console.log('DS POS ORIGIN: JavaScript cleanup loaded');
        });
        </script>
        <?php
    }
});

error_log("DS POS ORDER ORIGIN: Origin column display loaded successfully");



/**
 * Prevent Subscriptions from Inheriting $0.00 Total from Parent Orders
 * 
 * Problem: When parent order has 100% discount (coupon), the subscription
 * inherits the $0.00 total even though the subscription itself has no coupon.
 * This causes ATUM division by zero error during renewals.
 * 
 * Solution: Set subscription recurring total to minimum $0.01 when parent
 * order total is $0.00, preventing ATUM inventory calculation errors.
 * 
 * Add this to WordPress functions.php
 */

// Set minimum $0.01 for subscription recurring totals
add_action('woocommerce_checkout_subscription_created', function($subscription, $order, $recurring_cart) {
    // Check if subscription total is $0
    if ($subscription->get_total() == 0) {
        error_log('Subscription #' . $subscription->get_id() . ' has $0 total, setting to $0.01 minimum');
        
        // Set subscription total to $0.01
        $subscription->set_total('0.01');
        
        // Also update line item totals proportionally
        foreach ($subscription->get_items() as $item_id => $item) {
            $item->set_subtotal('0.01');
            $item->set_total('0.01');
            $item->save();
        }
        
        $subscription->save();
        error_log('Updated subscription #' . $subscription->get_id() . ' total to $0.01');
    }
}, 10, 3);

// Prevent $0 recurring totals when subscription is saved
add_action('woocommerce_subscription_object_updated_props', function($subscription, $updated_props) {
    // Only proceed if subscription is being updated
    if (!empty($updated_props)) {
        $total = $subscription->get_total();
        
        // If total is $0, set to $0.01
        if ($total == 0) {
            error_log('Preventing $0 total on subscription #' . $subscription->get_id() . ', setting to $0.01');
            $subscription->set_total('0.01');
            $subscription->save();
        }
    }
}, 10, 2);

// Fix existing $0 subscriptions when renewal is created
add_filter('wcs_renewal_order_created', function($renewal_order, $subscription) {
    // Check if renewal order total is $0
    if ($renewal_order->get_total() == 0) {
        error_log('Renewal order #' . $renewal_order->get_id() . ' for subscription #' . $subscription->get_id() . ' has $0 total');
        
        // Update the subscription recurring total to $0.01
        $subscription->set_total('0.01');
        
        // Update line items
        foreach ($subscription->get_items() as $item_id => $item) {
            $item->set_subtotal('0.01');
            $item->set_total('0.01');
            $item->save();
        }
        $subscription->save();
        
        // Update the renewal order to $0.01
        foreach ($renewal_order->get_items() as $item_id => $item) {
            $item->set_subtotal('0.01');
            $item->set_total('0.01');
            $item->save();
        }
        $renewal_order->set_total('0.01');
        $renewal_order->save();
        
        error_log('Updated subscription #' . $subscription->get_id() . ' and renewal order #' . $renewal_order->get_id() . ' to $0.01');
    }
    
    return $renewal_order;
}, 10, 2);

// Manual fix for existing $0 subscriptions
add_action('admin_init', function() {
    // Only run if specifically triggered (add ?fix_zero_subscriptions=1 to admin URL)
    if (isset($_GET['fix_zero_subscriptions']) && current_user_can('manage_woocommerce')) {
        
        // Get all active subscriptions with $0 total
        $args = array(
            'subscription_status' => array('active', 'on-hold'),
            'posts_per_page' => -1,
        );
        
        $subscriptions = wcs_get_subscriptions($args);
        $fixed_count = 0;
        
        foreach ($subscriptions as $subscription) {
            if ($subscription->get_total() == 0) {
                // Set to $0.01
                $subscription->set_total('0.01');
                
                // Update line items
                foreach ($subscription->get_items() as $item_id => $item) {
                    $item->set_subtotal('0.01');
                    $item->set_total('0.01');
                    $item->save();
                }
                
                $subscription->save();
                $fixed_count++;
                
                error_log('Fixed $0 subscription #' . $subscription->get_id());
            }
        }
        
        wp_die("Fixed {$fixed_count} subscriptions with $0 totals. They are now set to $0.01.");
    }
});

/**
 * Alternative: Set subscription line item prices to $0.01 during checkout
 * This prevents inheritance from parent order with 100% discount
 */
add_filter('woocommerce_subscription_cart_item_price', function($price, $cart_item, $cart_item_key) {
    // Check if the cart has a 100% discount coupon applied
    $cart = WC()->cart;
    if ($cart && $cart->get_total() == 0 && $cart->get_applied_coupons()) {
        // Override subscription item price to minimum $0.01
        $cart_item['data']->set_price(0.01);
        return wc_price(0.01);
    }
    return $price;
}, 10, 3);


/**
 * ATUM Multi Inventory - Bypass $0 Orders
 * 
 * Prevents ATUM from processing inventory for $0 orders to avoid division by zero errors.
 * This is a safety net in case subscriptions with $0 totals slip through.
 * 
 * Add this to functions.php AFTER the subscription $0.01 prevention code
 */

// Prevent ATUM from reducing stock on $0 orders
add_filter('atum/multi_inventory/should_reduce_stock', function($should_reduce, $order_id) {
    $order = wc_get_order($order_id);
    
    if ($order && $order->get_total() == 0) {
        error_log("ATUM BYPASS: Skipping inventory processing for $0 order #{$order_id}");
        return false;
    }
    
    return $should_reduce;
}, 999, 2); // High priority to override other hooks

// Prevent ATUM from processing order items inventory on $0 orders
add_filter('atum/multi_inventory/process_order_inventories', function($process, $order) {
    if ($order && $order->get_total() == 0) {
        error_log("ATUM BYPASS: Preventing inventory allocation for $0 order #{$order->get_id()}");
        return false;
    }
    
    return $process;
}, 999, 2);

// Catch ATUM inventory preparation before it crashes
add_action('atum/multi_inventory/before_prepare_order_items_inventories', function($order_id) {
    $order = wc_get_order($order_id);
    
    if ($order && $order->get_total() == 0) {
        error_log("ATUM BYPASS: Blocking prepare_order_items_inventories for $0 order #{$order_id}");
        
        // Throw exception to stop ATUM processing
        throw new Exception('ATUM inventory processing skipped for $0 order');
    }
}, 1, 1); // Run early

// Handle renewal orders specifically
add_filter('wcs_renewal_order_created', function($renewal_order, $subscription) {
    // If somehow a $0 renewal slips through, mark it to skip ATUM
    if ($renewal_order->get_total() == 0) {
        error_log("ATUM BYPASS: Marking $0 renewal order #{$renewal_order->get_id()} to skip ATUM inventory");
        $renewal_order->update_meta_data('_skip_atum_inventory', 'yes');
        $renewal_order->save();
    }
    
    return $renewal_order;
}, 999, 2);

error_log("ATUM BYPASS: $0 order protection loaded successfully");

/**
 * Auto-complete orders containing ONLY virtual products.
 *
 * WooCommerce core auto-completes virtual+downloadable orders via
 * WC_Order::payment_complete(), but subscription renewal orders and
 * some payment gateways skip that path. This hook catches any order
 * that transitions to "processing" and immediately completes it when
 * every line item's product is virtual (memberships, digital goods, etc.).
 *
 * Covers:
 *  - Subscription renewal orders (created by WooCommerce Subscriptions)
 *  - POS orders for virtual-only products
 *  - Any gateway that sets status to "processing" instead of "completed"
 */
add_action('woocommerce_order_status_processing', function($order_id) {
    $order = wc_get_order($order_id);
    if (!$order instanceof WC_Order) {
        return;
    }

    // Only act on paid orders
    if (!$order->is_paid()) {
        return;
    }

    $items = $order->get_items();
    if (empty($items)) {
        return;
    }

    foreach ($items as $item) {
        $product = $item->get_product();

        // If product can't be resolved, play it safe and stop
        if (!$product || !$product->is_virtual()) {
            return;
        }
    }

    if (!$order->has_status('completed')) {
        error_log("DS AUTO-COMPLETE: Order #{$order_id} contains only virtual products — auto-completing");
        $order->update_status('completed', 'Auto-completed: all items are virtual products.');
    }
}, 10, 1);
