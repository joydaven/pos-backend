-- get all customer membership data
SELECT 
    u.ID AS user_id,
    u.user_email,
    u.display_name,
    m.ID AS membership_id,
    m.post_status AS membership_status,
    pm_start.meta_value AS membership_start,
    pm_end.meta_value AS membership_end,
    m.post_parent AS plan_id,
    p_plan.post_title AS plan_name
FROM wp_users u
JOIN wp_posts m 
    ON m.post_author = u.ID 
   AND m.post_type = 'wc_user_membership'
LEFT JOIN wp_postmeta pm_start 
    ON pm_start.post_id = m.ID 
   AND pm_start.meta_key = '_start_date'
LEFT JOIN wp_postmeta pm_end 
    ON pm_end.post_id = m.ID 
   AND pm_end.meta_key = '_end_date'
LEFT JOIN wp_posts p_plan 
    ON p_plan.ID = m.post_parent
WHERE u.user_email = 'jennifer@doctorsstudio.com';

-- Get plan details (raw product IDs, no expansion)
SELECT 
    plan.ID AS plan_id,
    plan.post_title AS plan_name,
    access_method.meta_value AS grant_access_upon,
    COALESCE(length_type.meta_value, 'unlimited') AS membership_length_type,
    product_ids.meta_value AS raw_product_ids
FROM wp_posts plan
LEFT JOIN wp_postmeta access_method 
    ON access_method.post_id = plan.ID 
   AND access_method.meta_key = '_access_method'
LEFT JOIN wp_postmeta length_type 
    ON length_type.post_id = plan.ID 
   AND length_type.meta_key = '_access_length_type'
LEFT JOIN wp_postmeta product_ids
    ON product_ids.post_id = plan.ID
   AND product_ids.meta_key = '_product_ids'
WHERE plan.ID = 276618
  AND plan.post_type = 'wc_membership_plan';

-- Get plan details product if it has and selected purchase in grant access upon
SELECT ID AS product_id, post_title AS product_name
FROM wp_posts
WHERE ID IN (278574, 278787, 285171, 285172) -- this is all product id will be put depending on raw product ids (e.g a:4:{i:0;i:278574;i:1;i:278787;i:2;i:285171;i:3;i:285172;})
  AND post_type = 'product'
  AND post_status = 'publish';